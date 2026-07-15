# ETF 申购资金赚钱效应分析设计

## 目标

新增独立脚本 `analyze_etf.py`，将 ETF 份额变化与 `ETF.xlsx` 的单位净值、累计净值结合，计算截至评价日仍然赚钱的净申购资金占比，并输出可复核的数据表和图表。

## 输入与命令行

```text
python analyze_etf.py \
  [--shares data/etf_shares.parquet] \
  [--nav ETF.xlsx] \
  [--as-of YYYY-MM-DD] \
  [--output-dir output/etf_profitability]
```

- 份额表要求包含 `date`、`fund_code`、`fund_name`、`total_shares`。
- `ETF.xlsx` 每只 ETF 一个工作表，要求包含 `基金代码`、`基金名称`、`净值日期`、`单位净值(元)`、`累计净值(元)`。
- 未指定 `--as-of` 时，评价日取全部目标 ETF 均有累计净值的最新共同日期。
- 指定 `--as-of` 时，实际评价日取不晚于指定日期的最新共同日期，并在终端明确显示。

## 核心对象与动作

核心对象是“净申购批次”：某只 ETF 在某个交易日新增的正份额，以及该日净值、估算申购金额和截至评价日的含分红收益。

计算步骤：

1. 按 ETF 和日期排序，计算 `net_subscription_shares = total_shares.diff()`。
2. 首条记录没有前期基准，不作为申购；负值或零表示赎回/无净申购，不进入分析。
3. 用基金代码和日期精确匹配净值。
4. `estimated_subscription_amount = net_subscription_shares × unit_nav`。
5. `holding_return = as_of_cumulative_nav ÷ entry_cumulative_nav - 1`。
6. `holding_return > 0` 的批次标记为赚钱；等于零不计为赚钱。
7. `profitable_capital_ratio = 赚钱批次申购金额之和 ÷ 全部正净申购金额之和`。
8. 分别按 ETF 汇总，并以全部批次申购金额为权重计算总体比例。

累计净值仅用于含分红收益率；单位净值用于估算实际申购金额，避免把累计净值误当成成交价格。

## 输出

终端打印实际评价日以及各 ETF 与总体的赚钱资金比例、正申购金额和批次数。

输出目录包含：

- `etf_subscription_batches.parquet`：申购批次明细，包括申购日期、份额、单位净值、累计净值、估算金额、评价日累计净值、持有收益率和是否赚钱。
- `etf_profitability_summary.parquet`：各 ETF 与总体汇总，包括全部/赚钱申购金额、赚钱资金比例及批次数。
- `etf_profitability_summary.png`：各 ETF 与总体赚钱资金比例的横向柱状图。
- `etf_subscription_batches.png`：横轴为申购日期、纵轴为含分红收益率、点大小表示估算申购金额、颜色区分赚钱与未赚钱的批次分布图。

## 校验与错误处理

- 输入文件或必需字段缺失时立即失败。
- `(date, fund_code)` 重复、份额非正、净值非正时立即失败。
- 任一正申购日期缺少对应单位净值或累计净值时立即失败，不静默剔除。
- 指定日期之前不存在 7 只 ETF 的共同净值日时立即失败。
- 某只 ETF 没有正净申购时保留汇总行，比例为空，并在终端提示。
- 输出目录自动创建；脚本以非零状态码报告错误。

## 测试与验证

- 使用合成数据验证份额差分、首条排除、只保留正净申购。
- 验证申购金额加权比例和累计净值收益率。
- 验证默认及指定评价日的共同日期选择。
- 验证缺失净值、重复键、非正净值和无正申购路径。
- 在 `quant` Conda 环境运行 pytest 和 Ruff，并用现有 `ETF.xlsx` 与份额数据做真实数据烟雾测试。

## 范围外

- 申赎清单、盘中成交价、交易费用和申赎套利成本。
- 对赎回份额做 FIFO/LIFO 批次冲销。
- Excel 或交互式 HTML 报告。
