# ETF 历史净值下载与分析迁移设计

## 目标

通过 `api.md` 指定的 AkShare 接口下载 README 中 7 只 ETF 的完整历史单位净值与累计净值，保存为 `data/etf_nav.parquet`。分析 Notebook 改为直接读取该文件，不再依赖 `ETF.xlsx`。

## 下载入口与输出

- 新增 `download_etf_nav.py`，使用与现有份额下载脚本相同的 7 个基金代码。
- 对每个基金依次调用 `ak.fund_open_fund_info_em` 的“单位净值走势”和“累计净值走势”。
- 标准化输出字段为 `trade_date`、`fund_code`、`unit_nav`、`cumulative_nav`、`daily_return_pct`，按基金代码和日期升序写入 `data/etf_nav.parquet`。
- 单只基金下载失败时继续其余基金，最终写入 `data/etf_nav_failures.csv`，并在终端输出聚合进度、成功数和失败数。
- 本阶段不下载分红及拆分记录。

## 数据校验

- 基金代码和日期组合必须唯一，单位净值必须为正，日期必须升序。
- 接口的日增长率与单位净值计算的日变化率进行容差检查；异常只作为警告，不中断下载。
- 单位净值为空、基金代码格式错误或全部基金下载失败时抛出明确错误。

## Notebook 迁移

- `analyze_etf.ipynb` 的净值输入改为 `data/etf_nav.parquet`，不再读取 `ETF.xlsx`。
- 日期字段从 `trade_date` 映射为 Notebook 内部的 `date`；单位净值和累计净值沿用现有计算字段。
- 申购金额继续按“正份额变化 × 单位净值”估算。
- 本阶段仍以累计净值比较申购日与评价日来判断盈利，但 Notebook 正文明确标注该收益口径“需验证”：累计净值并非任意期间的复权总回报。后续如需严谨收益判断，应接入分红、拆分数据构造总回报序列。

## 验证

- 为净值标准化、合并、失败处理与校验添加 mock 测试。
- 在 `quant` 环境运行 pytest 与 Ruff。
- 对一只目标 ETF 执行真实接口烟雾测试，并执行 Notebook 验证新 Parquet 输入和中文展示。
