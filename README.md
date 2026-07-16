# 2026 年上半年赚钱效应数据分析

看看不同时点进来的资金赚钱的比例，比如不同时点申购的ETF，两融资金、公募偏股基金

## ETF

宽基指数:
易方达上证50、沪深300、中证500、中证1000、中证2000、红利低波、科创50

### 下载基金份额

```bash
conda run -n quant python download_etf.py
```

默认生成：

- `data/etf_shares.parquet`：沪深目标 ETF 合并后的日频份额。
- `data/sse_etf_shares_raw.parquet`：上交所原始数据。
- `data/szse_etf_shares_raw.parquet`：深交所原始数据。

### 分析申购资金赚钱比例

准备新的 `data/etf_shares.parquet` 和 `ETF.xlsx` 后，直接运行 Notebook：

```bash
conda run -n quant jupyter notebook analyze_etf.ipynb
```

Notebook 把每日正份额变化视为净申购，以“新增份额 × 当日单位净值”估算申购金额，并用累计净值计算截至评价日的含分红收益。赚钱资金比例为盈利批次申购金额占全部正净申购金额的比例。

图表和汇总表均在 Notebook 内联展示，标题、坐标轴、图例和说明均使用中文；不会生成分析 Parquet 或图片文件。
