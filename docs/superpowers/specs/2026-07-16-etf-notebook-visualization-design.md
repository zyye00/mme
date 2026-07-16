# ETF Notebook 展示与中文化设计

## 目标

将 ETF 申购资金赚钱效应的所有图表从命令行脚本迁移到 Jupyter Notebook 内联展示，且所有图中文字使用简体中文。

## 职责边界

- `analyze_etf.py` 只负责读取份额和净值、计算申购批次与资金赚钱比例、打印终端汇总、写入两份 Parquet。
- `analyze_etf.ipynb` 负责调用分析函数或读取已生成的 Parquet，并内联展示图表。
- 命令行脚本不再生成 PNG 文件，也不再依赖 Matplotlib。

## Notebook 内容

Notebook 按顺序包含：

1. 中文标题和计算口径说明。
2. 参数单元：份额文件、净值文件和可选评价日。
3. 执行分析并展示实际评价日、总体与单 ETF 汇总表。
4. “各 ETF 赚钱资金比例”横向柱状图：基金代码为纵轴，横轴为赚钱资金比例，包含“总体”行。
5. “申购批次收益分布”分面气泡图：每只 ETF 一行，横轴为申购日期，纵轴为截至评价日的含分红收益率，气泡大小为估算申购金额，绿色表示赚钱、红色表示未赚钱。

所有标题、坐标轴、图例、无数据提示和 Notebook 说明均采用简体中文。Matplotlib 字体配置会优先使用系统可用的中文字体；若环境缺少中文字体，Notebook 会显示明确的安装提示，不静默输出乱码。

## 输出

脚本保留：

- `etf_subscription_batches.parquet`
- `etf_profitability_summary.parquet`

Notebook 图表仅内联展示，不写入 PNG。

## 验证

- 更新单元测试，确保分析脚本不再创建 PNG。
- 用 `quant` 环境执行 Notebook，并验证每个单元可运行、图表标题和图例为中文。
- 继续运行 pytest 与 Ruff。
