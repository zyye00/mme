# 2026 年上半年赚钱效应数据分析

看看不同时点进来的资金赚钱的比例，比如不同时点申购的ETF，两融资金、公募偏股基金

## ETF

统计 7 个宽基或策略指数：上证50、沪深300、中证500、中证1000、中证2000、红利低波、科创50。每个指数包含多只精确跟踪的境内被动 ETF。

ETF 与指数的显式映射位于 `config/etf_universe.csv`，最后人工核对日期为 **2026-07-17**。当前清单共 100 只 ETF，仅纳入精确跟踪相应指数且在核对日仍上市交易的产品；增强、成长、价值、等权、联接基金、已终止上市产品及跟踪近似指数的产品不在统计范围。新上市 ETF 须人工核验后再更新该文件，不会自动纳入。

### 下载基金份额

```bash
conda run -n quant python download_etf_shares.py --start 2026-01-01
```

默认生成：

- `data/etf_shares.parquet`：沪深目标 ETF 合并后的日频份额。
- `data/sse_etf_shares_raw.parquet`：上交所原始数据。
- `data/szse_etf_shares_raw.parquet`：深交所原始数据。

下载脚本只保留显式映射内的 ETF。任一上交所交易日、深交所数据源或映射 ETF 缺失时，脚本返回非零状态，且不会用不完整结果覆盖旧 Parquet。

### 分析申购资金赚钱比例

先下载 ETF 净值：

```bash
conda run -n quant python download_etf_nav.py
```

准备 `data/etf_shares.parquet`、`data/etf_nav.parquet` 和 `data/etf_splits.parquet` 后，直接运行 Notebook：

```bash
conda run -n quant jupyter notebook analyze_etf.ipynb
```

Notebook 先在单只 ETF 层识别每日正份额变化，以“新增份额 × 当日单位净值”估算申购金额，然后按申购金额汇总到指数。一只 ETF 的赎回不会抵消另一只 ETF 的正净申购；新上市 ETF 的首条份额不计为申购。

所有 ETF 使用最新共同净值日作为统一评价日。Notebook 并排展示申购后 60 个净值交易日与申购后至今的收益；不足 60 个交易日的近期批次按至今计算。指数赚钱资金比例仍在 ETF 申购批次层判断盈亏，再用申购金额加总；同一指数、同一日期的气泡金额为各 ETF 申购金额之和，气泡收益为金额加权收益。

当前暂以累计净值判断申购资金是否赚钱；该收益口径需验证，累计净值并非任意期间的复权总回报。

主表和图表仅展示 7 个指数及“总体”，成分复核表展示“指数 → 管理人+指数 ETF”中文名称，不显示基金代码。所有图表和汇总表均在 Notebook 内联展示，标题、坐标轴、图例和说明均使用中文；不会生成分析 Parquet 或图片文件。如份额或净值数据缺少任一映射 ETF，Notebook 会列出基金简称并停止。

净值下载同时保存 ETF 拆分折算记录。Notebook 会按折算比例自动调整拆分日的净申购份额；若分析区间内存在拆分，会打印基金简称、日期、比例以及调整前后份额供人工复核。
