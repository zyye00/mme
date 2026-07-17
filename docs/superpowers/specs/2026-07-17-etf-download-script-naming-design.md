# ETF 下载脚本命名设计

## 目标

将当前 ETF 份额下载入口从 `download_etf.py` 重命名为 `download_etf_shares.py`，使其与 `download_etf_nav.py` 的职责清晰区分。

## 变更范围

- 移动实现文件为 `download_etf_shares.py`，保留现有命令行参数和份额下载行为。
- 更新测试导入、净值下载脚本中的目标基金代码引用、Notebook 的缺失文件提示和 README 命令。
- 不保留 `download_etf.py` 兼容包装，避免两个入口产生歧义。

## 验证

- 在 `quant` 环境运行全部 pytest 和 Ruff。
- 使用新的脚本名执行单日份额下载烟雾测试，确认输出不变。
