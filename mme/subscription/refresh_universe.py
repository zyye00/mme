"""Refresh industry ETF groups from authoritative tracking targets."""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import akshare as ak
import pandas as pd

BASE_COLUMNS = ["index_code", "index_name", "index_order", "fund_code", "fund_name"]
UNIVERSE_COLUMNS = [*BASE_COLUMNS, "tracking_target"]
INDUSTRIES = {
    "半导体": ("industry_semiconductor", 8, "半导体"),
    "有色金属": ("industry_nonferrous", 9, "有色"),
    "电力": ("industry_power", 10, "电力"),
    "医药": ("industry_medicine", 11, "医药"),
}
INDUSTRY_EXCLUSIONS = {"医药": ("医疗", "创新药", "生物医药")}
CODE_PATTERN = re.compile(r"\d{6}")
MAX_REQUEST_ATTEMPTS = 3


def normalize_code(value: object) -> str:
    match = CODE_PATTERN.search(str(value))
    if not match:
        raise ValueError(f"无法解析基金代码：{value!r}")
    return match.group()


def latest_candidate_codes(sse_raw_path: Path, szse_raw_path: Path) -> set[str]:
    sse = pd.read_parquet(sse_raw_path)
    szse = pd.read_parquet(szse_raw_path)
    required_sse = {"统计日期", "基金代码"}
    required_szse = {"日期", "基金代码"}
    if missing_columns := required_sse - set(sse.columns):
        raise ValueError(f"上交所原始份额数据缺少字段：{', '.join(sorted(missing_columns))}")
    if missing_columns := required_szse - set(szse.columns):
        raise ValueError(f"深交所原始份额数据缺少字段：{', '.join(sorted(missing_columns))}")
    sse_dates = pd.to_datetime(sse["统计日期"], errors="raise")
    szse_dates = pd.to_datetime(szse["日期"], errors="raise")
    sse_codes = sse.loc[sse_dates.eq(sse_dates.max()), "基金代码"].map(normalize_code)
    szse_codes = szse.loc[szse_dates.eq(szse_dates.max()), "基金代码"].map(normalize_code)
    return set(sse_codes) | set(szse_codes)


def fetch_overview(
    fund_code: str,
    fetch: Callable[[str], pd.DataFrame] = ak.fund_overview_em,
) -> dict[str, str]:
    overview = fetch(fund_code)
    if overview.empty:
        raise ValueError("接口未返回基金概况")
    required_columns = {"基金代码", "基金简称", "跟踪标的"}
    if missing_columns := required_columns - set(overview.columns):
        raise ValueError(f"基金概况缺少字段：{', '.join(sorted(missing_columns))}")
    record = overview.iloc[0]
    returned_code = normalize_code(record["基金代码"])
    if returned_code != fund_code:
        raise ValueError(f"接口返回代码 {returned_code} 与请求代码不一致")
    fund_name = str(record["基金简称"]).strip()
    tracking_target = str(record["跟踪标的"]).strip()
    if not fund_name or fund_name == "nan" or not tracking_target or tracking_target == "nan":
        raise ValueError("基金简称或跟踪标的为空")
    return {"fund_code": fund_code, "fund_name": fund_name, "tracking_target": tracking_target}


def fetch_overviews(
    fund_codes: set[str],
    fetch: Callable[[str], pd.DataFrame] = ak.fund_overview_em,
    max_workers: int = 8,
    progress: Callable[[str], None] = print,
    cache_path: Path | None = None,
) -> pd.DataFrame:
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    cached = _load_cache(cache_path)
    codes_to_fetch = fund_codes - set(cached["fund_code"])
    records: list[dict[str, str]] = cached.to_dict("records")
    failures: list[str] = []

    def fetch_with_retry(fund_code: str) -> dict[str, str]:
        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                return fetch_overview(fund_code, fetch)
            except Exception:
                if attempt == MAX_REQUEST_ATTEMPTS:
                    raise
                time.sleep(0.5 * attempt)
        raise RuntimeError("unreachable")

    sorted_codes = sorted(codes_to_fetch)
    progress(f"基金概况缓存：{len(cached)}/{len(fund_codes)} 已可复用")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_with_retry, fund_code): fund_code for fund_code in sorted_codes}
        for index, future in enumerate(as_completed(futures), start=1):
            fund_code = futures[future]
            try:
                records.append(future.result())
            except Exception as error:
                failures.append(f"{fund_code}：{error}")
            if index % 50 == 0 or index == len(futures):
                progress(f"基金概况：{index}/{len(futures)} 完成，失败 {len(failures)} 只")
                if cache_path:
                    checkpoint = pd.DataFrame(records).drop_duplicates("fund_code", keep="last")
                    _write_csv_atomic(cache_path, checkpoint)
    result = pd.DataFrame(records).drop_duplicates("fund_code", keep="last").sort_values("fund_code").reset_index(drop=True)
    if cache_path:
        _write_csv_atomic(cache_path, result)
    if failures:
        raise RuntimeError(f"基金概况下载失败：{'；'.join(failures)}")
    return result


def _load_cache(path: Path | None) -> pd.DataFrame:
    if not path or not path.exists():
        return pd.DataFrame(columns=["fund_code", "fund_name", "tracking_target"])
    cache = pd.read_csv(path, dtype=str)
    expected_columns = ["fund_code", "fund_name", "tracking_target"]
    if cache.columns.tolist() != expected_columns:
        raise ValueError("基金概况缓存字段不符合预期")
    cache["fund_code"] = cache["fund_code"].map(normalize_code)
    return cache


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        frame.to_csv(temporary_path, index=False, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_existing_universe(path: Path) -> pd.DataFrame:
    universe = pd.read_csv(path, dtype=str)
    if universe.columns.tolist() not in [BASE_COLUMNS, UNIVERSE_COLUMNS]:
        raise ValueError("现有 ETF 映射字段不符合预期")
    universe = universe.loc[:, BASE_COLUMNS].copy()
    universe["index_order"] = pd.to_numeric(universe["index_order"], errors="raise")
    industry_codes = {index_code for index_code, _, _ in INDUSTRIES.values()}
    return universe.loc[~universe["index_code"].isin(industry_codes)].copy()


def classify_industry_etfs(overviews: pd.DataFrame, existing_codes: set[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in overviews.itertuples(index=False):
        matches = [
            name
            for name, (_, _, keyword) in INDUSTRIES.items()
            if keyword in record.tracking_target
            and not any(exclusion in record.tracking_target for exclusion in INDUSTRY_EXCLUSIONS.get(name, ()))
        ]
        if len(matches) > 1:
            raise ValueError(f"基金 {record.fund_code} 命中多个行业：{'、'.join(matches)}")
        if not matches or record.fund_code in existing_codes:
            continue
        industry = matches[0]
        index_code, index_order, _ = INDUSTRIES[industry]
        rows.append(
            {
                "index_code": index_code,
                "index_name": industry,
                "index_order": index_order,
                "fund_code": record.fund_code,
                "fund_name": record.fund_name,
                "tracking_target": record.tracking_target,
            }
        )
    return pd.DataFrame(rows, columns=UNIVERSE_COLUMNS).sort_values(["index_order", "fund_code"]).reset_index(drop=True)


def build_universe(existing: pd.DataFrame, overviews: pd.DataFrame) -> pd.DataFrame:
    overview_map = overviews.set_index("fund_code")
    missing_codes = set(existing["fund_code"]) - set(overview_map.index)
    if missing_codes:
        raise ValueError(f"缺少既有映射基金的概况：{', '.join(sorted(missing_codes))}")
    base = existing.copy()
    base["index_order"] = pd.to_numeric(base["index_order"], errors="raise")
    base["tracking_target"] = base["fund_code"].map(overview_map["tracking_target"])
    industry = classify_industry_etfs(overviews, set(base["fund_code"]))
    universe = pd.concat([base, industry], ignore_index=True)
    if universe["fund_code"].duplicated().any():
        raise ValueError("扩展后的 ETF 映射包含重复基金代码")
    return universe.sort_values(["index_order", "fund_code"]).reset_index(drop=True)


def refresh_etf_universe(
    universe_path: Path,
    sse_raw_path: Path,
    szse_raw_path: Path,
    fetch: Callable[[str], pd.DataFrame] = ak.fund_overview_em,
    max_workers: int = 8,
    progress: Callable[[str], None] = print,
    cache_path: Path | None = None,
) -> pd.DataFrame:
    existing = load_existing_universe(universe_path)
    candidate_codes = latest_candidate_codes(sse_raw_path, szse_raw_path)
    progress(f"候选 ETF：{len(candidate_codes)} 只，开始查询跟踪标的")
    overviews = fetch_overviews(candidate_codes, fetch, max_workers, progress, cache_path)
    universe = build_universe(existing, overviews)
    _write_csv_atomic(universe_path, universe)
    review = universe.loc[universe["index_name"].isin(INDUSTRIES), ["index_name", "fund_name", "tracking_target"]]
    progress("行业 ETF 复核表：")
    progress(review.rename(columns={"index_name": "行业", "fund_name": "基金名称", "tracking_target": "跟踪标的"}).to_string(index=False))
    return universe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=Path("config/etf_universe.csv"))
    parser.add_argument("--sse-raw", type=Path, default=Path("data/source/subscription/sse_etf_shares_raw.parquet"))
    parser.add_argument("--szse-raw", type=Path, default=Path("data/source/subscription/szse_etf_shares_raw.parquet"))
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--cache", type=Path, default=Path("data/source/subscription/etf_overviews.csv"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        refresh_etf_universe(
            args.universe,
            args.sse_raw,
            args.szse_raw,
            max_workers=args.max_workers,
            cache_path=args.cache,
        )
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
