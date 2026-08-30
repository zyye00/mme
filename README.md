# ETF 净申购与融资买入批次收益研究

本项目研究不同日期形成的 ETF 净申购批次和融资买入批次，在统一持有假设下有多少资金获得正收益，以及结论对观察窗口和样本分组有多敏感。

- [ETF 净申购分析](notebooks/etf_subscription_profitability.ipynb)
- [融资买入分析](notebooks/margin_profitability.ipynb)

## 项目概览

| 研究模块 | 样本与范围 | 当前评价日 |
|---|---|---|
| ETF 净申购 | 180 只境内被动 ETF，11 个分类，7,063 个申购批次 | 2026-07-24 |
| 融资买入 | 1,014 只固定样本证券，并对 2026-01-05 首日融资买入额分组 | 2026-07-24 |

> **ETF 完整 60 日结果**
>
> 3,359 / 7,063 个申购批次已完成 60 个净值交易日，完成率 47.56%；按申购金额加权的正收益资金占比为 **61.27%**。若把未满 60 日批次也按 2026-07-24 的最新净值评价，正收益资金占比为 **24.77%**。

> **融资买入结果**
>
> 固定样本共 1,014 只证券，首日覆盖全部融资买入额的 **80.00%**；后续交易日覆盖率中位数为 **70.68%**。截至 2026-07-24，统一持有假设下的累计代理正收益资金占比为 **26.73%**。
>
> 分组方面，在 2026-01-05 固定选出“最高 10 只组”和“中位附近 10 只组”，并持有至 2026-07-24；两组的代理正收益资金占比分别为 **30.49%** 和 **6.79%**。

## 工作流程

- 建立可从空数据目录运行的下载、校验、派生和 Notebook 分析流水线。
- 将 180 只 ETF 显式映射到 11 个分类：7 个宽基或策略指数，加 4 个行业类别。
- 统一处理 ETF 份额、净值、拆分折算和现金分红，区分“完整 60 日”和“截至评价日”两种观察口径。
- 从沪深交易所公开两融明细构造固定样本，并用前复权行情计算融资买入批次收益代理。
- 将首日分组定义与逐日行情分表存储，通过 `exchange + security_code` 关联；下载失败时保留部分结果且不覆盖旧完整文件。
- 将 `511360`、`511520` 明确列为低风险债券 ETF 研究范围排除项，并对高、低组使用同一规则；其他证券均保留，同期选择、同期评价仅作为样本内探索。
- 为关键数据约束、失败恢复、计算口径和 Notebook 自顶向下执行编写自动化测试。

## 结论边界

- ETF 净申购金额由份额变化和单位净值估算。
- 融资买入批次价格使用当日成交均价近似。
- 正收益资金占比是统一持有假设下的研究代理，不能解释为投资者真实盈亏。
- 首日分组是固定样本的描述性比较；60 日滚动高低组使用同一时期的数据选样和评价，只用于样本内探索，不是样本外预测或回测。
- 数据源、复权方式、停牌和新上市证券都会影响可用样本。

## 从空数据目录复现

项目依赖声明在 `pyproject.toml`。请自行准备包含项目依赖和 Jupyter 的 Python 3.11 及以上环境，从仓库根目录按顺序运行。数据目录无需预先创建。

### ETF 净申购流水线

仓库已包含人工复核的 ETF 清单 `config/etf_universe.csv`，常规复现不需要刷新清单。

```bash
python -m mme.subscription.download_shares \
  --start 2026-01-01 \
  --end 2026-07-25

python -m mme.subscription.download_nav \
  --end 2026-07-25 \
  --dividend-start 2026-01-01 \
  --dividend-end 2026-07-25

jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  notebooks/etf_subscription_profitability.ipynb
```

| 步骤 | 主要输入 | 主要输出 |
|---|---|---|
| 下载 ETF 份额 | `config/etf_universe.csv` | `etf_shares.parquet` 及沪深交易所原始份额文件 |
| 下载净值、拆分和分红 | `config/etf_universe.csv` | `etf_nav.parquet`、`etf_splits.parquet`、`etf_dividends.parquet` |
| 执行 ETF Notebook | 上述数据和 ETF 清单 | 刷新 Notebook 内的表格与图表 |

### 融资买入批次收益代理流水线

```bash
python -m mme.margin.download_details \
  --start 2026-01-01 \
  --end 2026-07-25

python -m mme.margin.download_security_basics
python -m mme.margin.download_security_industries
python -m mme.margin.summarize_first_day

python -m mme.margin.download_prices \
  --input data/derived/margin/first_day_top80.parquet \
  --output data/source/margin/first_day_top80_prices.parquet \
  --start 2026-01-05 \
  --end 2026-07-25

python -m mme.margin.download_prices \
  --input data/derived/margin/first_day_financing_tiers.parquet \
  --output data/source/margin/first_day_financing_tier_prices.parquet \
  --start 2026-01-05 \
  --end 2026-07-25

jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  notebooks/margin_profitability.ipynb
```

| 步骤 | 主要输入 | 主要输出 |
|---|---|---|
| 下载两融明细 | 交易日历、沪深交易所公开接口 | `margin_financing_buy.parquet` |
| 下载证券信息 | BaoStock | `baostock_security_basics.parquet`、`baostock_security_industries.parquet` |
| 生成固定样本和分组 | 两融明细、证券基础信息 | `first_day_top80.parquet`、`first_day_financing_tiers.parquet` |
| 下载样本行情 | 上一步的两份样本 | `first_day_top80_prices.parquet`、`first_day_financing_tier_prices.parquet` |
| 执行两融 Notebook | 两融明细、样本、行情和证券信息 | 刷新 Notebook，并生成 `data/derived/margin/` 下的分析表 |

## 研究口径

### ETF 净申购

ETF 清单最后核对日期为 2026-07-20。11 个分类包括上证 50、沪深 300、中证 500、中证 1000、中证 2000、红利低波、科创 50，以及半导体、有色金属、电力、医药 4 个行业类别。每只 ETF 都在 `config/etf_universe.csv` 中显式归类。

Notebook 在单只 ETF 层识别每日正份额变化，以“新增份额 × 当日单位净值”估算申购金额；一只 ETF 的赎回不会抵消另一只 ETF 的正申购，新上市 ETF 的首条份额不计为申购。收益计算纳入拆分折算和满足登记日、发放日条件的税前现金分红，未计税费和交易费用。

所有 ETF 使用最新共同净值日作为评价日。“完整 60 日”只统计已经历 60 个净值交易日的批次；“截至评价日”同时包含近期未满 60 日的批次。分类汇总仍先在 ETF 批次层判断盈亏，再按申购金额加总。

### 融资买入

每个证券交易日的正融资买入额被视为一个批次。买入价以当日全市场成交金额除以成交量近似，并按行情前复权比例换算；每个批次假设持续持有到相应评价日，以前复权收盘价计算收益代理。

首日固定样本选取累计覆盖 80% 融资买入额的 1,014 只证券。首日融资买入额分组另存为 20 行定义表，逐日行情另存为 2,680 行行情表，两表按 `exchange + security_code` 多对一关联，分组确定日和行情交易日不共用同一日期字段。

60 日滚动高低组预先排除 `511360`（短融 ETF）和 `511520`（政金债 ETF），因为本节不研究低风险债券；这条规则同时用于高、低两组，其他证券不因 ETF 身份被排除。随后按同一窗口内的代理正收益资金占比排序并展示同期结果。这个设计用于发现样本内结构，不具备样本外检验所需的时间隔离。

## 数据来源

- [上海证券交易所：融资融券交易明细](https://www.sse.com.cn/market/othersdata/margin/detail/index.shtml)
- [深圳证券交易所：融资融券交易](https://www.szse.cn/www/marketServices/deal/finance/index.html)
- [AkShare 文档](https://akshare.akfamily.xyz/)：ETF 概况、净值、拆分和分红等公开数据接口
- [BaoStock](https://www.baostock.com/)：证券基础信息、行业信息和日频行情

公开接口可能调整字段、限流或修订历史数据；复现时应记录下载日期，并重新核对 Notebook 中的样本范围和评价日。
