"""Export the first-day 80% margin-financing sample for all securities by type."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from mme.common.output import write_parquet_outputs

REQUIRED_COLUMNS = {"trade_date", "exchange", "security_code", "security_name", "financing_buy_amount"}
SECURITY_TYPE_NAMES = {
    "stock": "股票",
    "etf": "ETF",
    "convertible_bond": "可转债",
    "index": "指数",
    "unknown": "未匹配",
}


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


def annotate_security_types(selected: pd.DataFrame, basics: pd.DataFrame) -> pd.DataFrame:
    required_basics = {"security_code", "exchange", "security_type"}
    missing = required_basics - set(basics.columns)
    if missing:
        raise ValueError(f"security basics is missing columns: {', '.join(sorted(missing))}")

    selected = selected.merge(
        basics[["security_code", "exchange", "security_type"]],
        on=["security_code", "exchange"],
        how="left",
        validate="one_to_one",
    )
    selected["security_type"] = selected["security_type"].fillna("unknown")
    selected["security_type_name"] = selected["security_type"].map(SECURITY_TYPE_NAMES).fillna(selected["security_type"])
    selected["security_type_order"] = selected["security_type"].map({key: index for index, key in enumerate(SECURITY_TYPE_NAMES)}).fillna(len(SECURITY_TYPE_NAMES))
    selected = selected.sort_values(
        ["security_type_order", "financing_buy_amount"],
        ascending=[True, False],
        kind="stable",
    ).reset_index(drop=True)
    selected["type_rank"] = selected.groupby("security_type", sort=False).cumcount() + 1
    return selected


def summarize_security_types(selected: pd.DataFrame) -> pd.DataFrame:
    total = selected["financing_buy_amount"].sum()
    summary = (
        selected.groupby(["security_type_order", "security_type", "security_type_name"], sort=True)
        .agg(
            security_count=("security_code", "size"),
            financing_buy_amount=("financing_buy_amount", "sum"),
        )
        .reset_index()
    )
    summary["sample_amount_ratio"] = summary["financing_buy_amount"] / total
    return summary.sort_values("security_type_order", kind="stable").reset_index(drop=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/source/margin/margin_financing_buy.parquet"))
    parser.add_argument("--basics", type=Path, default=Path("data/source/security/baostock_security_basics.parquet"))
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument(
        "--data-output",
        type=Path,
        default=Path("data/derived/margin/first_day_top80.parquet"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output/margin"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        selected, summary = analyze_first_day(pd.read_parquet(args.input), args.threshold)
        selected = annotate_security_types(selected, pd.read_parquet(args.basics))
        type_summary = summarize_security_types(selected)
        write_parquet_outputs({args.data_output: selected})
        args.output_dir.mkdir(parents=True, exist_ok=True)
        detail_path = args.output_dir / "first_day_top80_by_type.csv"
        summary_path = args.output_dir / "first_day_top80_summary.csv"
        type_summary_path = args.output_dir / "first_day_top80_type_summary.csv"
        selected.to_csv(detail_path, index=False, encoding="utf-8-sig")
        summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
        type_summary.to_csv(type_summary_path, index=False, encoding="utf-8-sig")
        first = selected.iloc[0]
        print(
            f"first_day={first['trade_date']} threshold={args.threshold:.1%} "
            f"security_count={len(selected)} cumulative_ratio={summary.loc[0, 'amount_ratio']:.2%}"
        )
        print(summary.to_string(index=False))
        print(type_summary.to_string(index=False))
        print(f"Output: {args.data_output}\nOutput: {detail_path}\nOutput: {summary_path}\nOutput: {type_summary_path}")
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
