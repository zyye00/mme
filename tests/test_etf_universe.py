from pathlib import Path

import pandas as pd
import pytest

from etf_universe import ETF_UNIVERSE, UNIVERSE_COLUMNS, load_etf_universe


def test_reviewed_universe_contains_expected_funds() -> None:
    counts = ETF_UNIVERSE.groupby("index_name")["fund_code"].size().to_dict()

    assert counts == {
        "上证50": 11,
        "沪深300": 25,
        "中证500": 22,
        "中证1000": 7,
        "中证2000": 10,
        "红利低波": 8,
        "科创50": 17,
        "半导体": 18,
        "有色金属": 25,
        "电力": 23,
        "医药": 14,
    }
    assert len(ETF_UNIVERSE) == 180


def test_load_etf_universe_rejects_duplicate_fund_code(tmp_path: Path) -> None:
    universe = ETF_UNIVERSE.copy()
    universe.loc[1, "fund_code"] = universe.loc[0, "fund_code"]
    path = tmp_path / "universe.csv"
    universe.to_csv(path, index=False, columns=UNIVERSE_COLUMNS)

    with pytest.raises(ValueError, match="重复基金代码"):
        load_etf_universe(path)


def test_load_etf_universe_rejects_wrong_columns(tmp_path: Path) -> None:
    path = tmp_path / "universe.csv"
    pd.DataFrame({"fund_code": ["510050"]}).to_csv(path, index=False)

    with pytest.raises(ValueError, match="ETF 映射字段"):
        load_etf_universe(path)


def test_load_etf_universe_rejects_index_code_name_conflict(tmp_path: Path) -> None:
    universe = ETF_UNIVERSE.copy()
    universe.loc[universe.index[0], "index_name"] = "错误指数"
    path = tmp_path / "universe.csv"
    universe.to_csv(path, index=False, columns=UNIVERSE_COLUMNS)

    with pytest.raises(ValueError, match="指数代码与名称"):
        load_etf_universe(path)


def test_load_etf_universe_rejects_index_name_code_conflict(tmp_path: Path) -> None:
    universe = ETF_UNIVERSE.copy()
    universe.loc[universe.index[0], "index_name"] = "沪深300"
    path = tmp_path / "universe.csv"
    universe.to_csv(path, index=False, columns=UNIVERSE_COLUMNS)

    with pytest.raises(ValueError, match="指数代码与名称|指数名称与代码"):
        load_etf_universe(path)
