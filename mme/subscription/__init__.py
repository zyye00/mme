"""ETF subscription research."""

from .profitability import EtfProfitabilityResult, calculate_etf_profitability
from .universe import ETF_UNIVERSE, TARGET_FUND_CODES, load_etf_universe

__all__ = [
    "ETF_UNIVERSE",
    "TARGET_FUND_CODES",
    "EtfProfitabilityResult",
    "calculate_etf_profitability",
    "load_etf_universe",
]
