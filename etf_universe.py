"""Load the reviewed ETF-to-index universe."""

from pathlib import Path

import pandas as pd

UNIVERSE_PATH = Path(__file__).with_name("config") / "etf_universe.csv"
UNIVERSE_COLUMNS = ["index_code", "index_name", "index_order", "fund_code", "fund_name"]
EXPECTED_INDEXES = {
    "000016": ("上证50", 1),
    "000300": ("沪深300", 2),
    "000905": ("中证500", 3),
    "000852": ("中证1000", 4),
    "932000": ("中证2000", 5),
    "H30269": ("红利低波", 6),
    "000688": ("科创50", 7),
}


def load_etf_universe(path: Path = UNIVERSE_PATH) -> pd.DataFrame:
    universe = pd.read_csv(path, dtype=str)
    if universe.columns.tolist() != UNIVERSE_COLUMNS:
        raise ValueError(f"ETF 映射字段必须为：{', '.join(UNIVERSE_COLUMNS)}")
    if universe.empty or universe.isna().any().any():
        raise ValueError("ETF 映射不能为空或包含空值")

    universe = universe.apply(lambda column: column.str.strip())
    if universe.eq("").any().any():
        raise ValueError("ETF 映射不能包含空字符串")
    if not universe["fund_code"].str.fullmatch(r"\d{6}").all():
        raise ValueError("ETF 映射中的基金代码必须为 6 位数字")
    if universe["fund_code"].duplicated().any():
        duplicates = universe.loc[universe["fund_code"].duplicated(False), "fund_code"].unique()
        raise ValueError(f"ETF 映射包含重复基金代码：{', '.join(sorted(duplicates))}")

    universe["index_order"] = pd.to_numeric(universe["index_order"], errors="raise").astype(int)
    index_definitions = universe[["index_code", "index_name", "index_order"]].drop_duplicates()
    if index_definitions["index_code"].duplicated().any():
        raise ValueError("ETF 映射中的指数代码与名称或顺序冲突")
    if index_definitions["index_name"].duplicated().any():
        raise ValueError("ETF 映射中的指数名称与代码或顺序冲突")
    if index_definitions["index_order"].duplicated().any():
        raise ValueError("ETF 映射中的指数顺序重复")
    actual_indexes = {
        row.index_code: (row.index_name, row.index_order)
        for row in index_definitions.itertuples()
    }
    if actual_indexes != EXPECTED_INDEXES:
        raise ValueError("ETF 映射的指数代码、名称或顺序与约定的 7 个指数不一致")

    return universe.sort_values(["index_order", "fund_code"]).reset_index(drop=True)


ETF_UNIVERSE = load_etf_universe()
TARGET_FUND_CODES = frozenset(ETF_UNIVERSE["fund_code"])
