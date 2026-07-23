# 2026 年上半年赚钱效应数据分析

看看不同时点进来的资金赚钱的比例，比如不同时点申购的ETF，两融资金、公募偏股基金

## ETF 申购

统计 7 个宽基或策略指数：上证50、沪深300、中证500、中证1000、中证2000、红利低波、科创50，以及半导体、有色金属、电力、医药 4 个行业组。每个组包含多只精确跟踪的境内被动 ETF。

ETF 与分组的显式映射位于 `config/etf_universe.csv`，最后跟踪标的核对日期为 **2026-07-20**。当前清单共 180 只 ETF；行业 ETF 仅按 AkShare 概况接口的“跟踪标的”归类：半导体、有色、电力和医药使用严格关键词，医药排除医疗、创新药和生物医药等近似主题。新上市 ETF 须运行 `mme.subscription.refresh_universe` 并人工复核后再纳入。

### 下载基金份额

```bash
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.subscription.download_shares --start 2026-01-01
```

默认生成：

- `data/source/subscription/etf_shares.parquet`：沪深目标 ETF 合并后的日频份额。
- `data/source/subscription/sse_etf_shares_raw.parquet`：上交所原始数据。
- `data/source/subscription/szse_etf_shares_raw.parquet`：深交所原始数据。

下载脚本只保留显式映射内的 ETF。任一上交所交易日、深交所数据源或映射 ETF 缺失时，脚本返回非零状态，且不会用不完整结果覆盖旧 Parquet。

### 分析申购资金赚钱比例

先下载 ETF 净值：

```bash
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.subscription.download_nav
```

准备 `data/source/subscription/` 下的份额、净值、拆分和分红数据后，直接运行 Notebook：

```bash
"$HOME/miniforge3/bin/conda" run -n quant jupyter notebook notebooks/etf_subscription_profitability.ipynb
```

Notebook 先在单只 ETF 层识别每日正份额变化，以“新增份额 × 当日单位净值”估算申购金额，然后按申购金额汇总到指数。一只 ETF 的赎回不会抵消另一只 ETF 的正净申购；新上市 ETF 的首条份额不计为申购。

所有 ETF 使用最新共同净值日作为统一评价日。Notebook 并排展示申购后 60 个净值交易日与申购后至今的收益；不足 60 个交易日的近期批次按至今计算。指数赚钱资金比例仍在 ETF 申购批次层判断盈亏，再用申购金额加总；同一指数、同一日期的气泡金额为各 ETF 申购金额之和，气泡收益为金额加权收益。

净值下载同时下载当年指数型股票基金的现金分红，并与净值、拆分记录一起原子更新。收益以单位净值和税前已到账现金分红计算；申购日早于权益登记日且分红发放日不晚于收益截止日时，才计入该批次。现金分红不再投资，不计税费和交易费用。Notebook 会展示已计入分红的中文复核表。

主表和图表仅展示 7 个指数及“总体”，成分复核表展示“指数 → 管理人+指数 ETF”中文名称，不显示基金代码。所有图表和汇总表均在 Notebook 内联展示，标题、坐标轴、图例和说明均使用中文；不会生成分析 Parquet 或图片文件。如份额或净值数据缺少任一映射 ETF，Notebook 会列出基金简称并停止。

净值下载同时保存 ETF 拆分折算记录。Notebook 会按折算比例自动调整拆分日的净申购份额；若分析区间内存在拆分，会打印基金简称、日期、比例以及调整前后份额供人工复核。

## 两融融资分析

全证券融资买入资金的分析结果见 [全证券融资买入资金赚钱效应 Notebook](notebooks/all_security_margin_profitability.ipynb)。该 Notebook 展示首日累计覆盖 80% 融资买入额的固定样本、证券类型统计、日度样本资金占比与累计赚钱资金比例。

ETF 专项结果见 [ETF 融资买入资金赚钱效应 Notebook](notebooks/etf_margin_profitability.ipynb)。

### 复现全证券结果

依次下载两融明细和 BaoStock 证券基础信息；随后在首个交易日按融资买入额从高到低选取累计达到 80% 的最小样本，并下载这批证券的日线行情：

```bash
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.margin.download_details
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.margin.download_security_basics
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.margin.summarize_first_day
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.margin.download_prices \
  --input data/derived/margin/first_day_top80_all_securities.parquet \
  --output data/source/margin/first_day_top80_all_securities_prices.parquet \
  --request-log data/state/baostock/all_security_price_requests.csv
"$HOME/miniforge3/bin/conda" run -n quant jupyter notebook notebooks/all_security_margin_profitability.ipynb
```

两融明细和基础信息分别保存在 `data/source/margin/margin_financing_buy.parquet`、`data/source/security/baostock_security_basics.parquet`。固定样本保存在 `data/derived/margin/first_day_top80_all_securities.parquet`，对应行情保存在 `data/source/margin/first_day_top80_all_securities_prices.parquet`；按证券类型聚类、供人工查看的样本清单位于 `output/margin/first_day_top80_all_securities_by_type.csv`。

### 复现 ETF 专项结果

在下载上述两融明细与证券基础信息后，执行以下命令筛选 ETF、下载行情并打开 Notebook：

```bash
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.margin.build_etf_details
"$HOME/miniforge3/bin/conda" run -n quant python -m mme.margin.download_prices
"$HOME/miniforge3/bin/conda" run -n quant jupyter notebook notebooks/etf_margin_profitability.ipynb
```
