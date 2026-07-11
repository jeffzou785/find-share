# Find Share 进度记录

> 更新日期：2026-07-11
> 用途：记录已完成工作、当前可复现状态和后续接手顺序。详细策略设计见 `improvements.md`，使用方式见 `README.md`。

## 1. 已完成

### 1.1 P0 数据与审计闭环

- `p0-audit --period 2025A --strategy all` 已通过。
- 人工标注已覆盖最新 7 只 `hit/watch`；策略二A已有 8 条带来源证据的 VBP 事件和 30 条 ground truth。
- 策略三海外收入解析具备 PDF、F10 fallback、golden case 和 parser-review 质量池。
- A/H 映射层已建立，港股数据接入使用 `$global-stock-data` 的前置已具备。

### 1.2 策略一：消费周期反转

- 财务口径固定为 2024Q1 至 2026Q1 的最近 9 期；历史筛选会按目标 `period` 截断，避免未来财报穿越。
- PE/PB 支持 3/5/10 年分位；财务数据由 `$a-stock-data` 获取，历史估值使用 AkShare/东财序列。
- 营运质量已覆盖经营现金流、扣非/归母利润背离、应收账款、存货和销售费用率，并输出独立 watch/reject 原因码。
- 消费池已改为多字段准入：`sw_first`、`sw_second` 精确匹配，加上 `em2016` 中的"日用化学品"补充美护/个护公司。
- 已兼容东财实际一级标签"纺织服装""休闲、生活及专业服务"，避免文档定义的消费子行业在运行时漏选。
- `refresh` 的历史样本状态改为按 DuckDB 写入后的去重行数判断；`insufficient` 与策略侧的 `pe_history_missing` 使用同一口径。

### 1.3 策略一历史数据预热

本轮把消费池 5 个核心行业全部跑通：**599/599 codes 已 ≥100 条 PE/PB 样本**（仅 001312 因 2026-04 新上市客观不足），全市场 ≥100 样本的 code 数从 200 提升到 **673**。

| 行业 | 池内数 | 已预热 | 仍缺 |
|---|---:|---:|---:|
| 食品饮料 | 142 | 142 | 0 |
| 休闲、生活及专业服务（服务消费） | 118 | 118 | 0 |
| 轻工制造 | 145 | 144 | 1（数据源不足） |
| 商贸零售 | 99 | 99 | 0 |
| 纺织服装 | 95 | 94 | 1（001312 新上市） |

本轮 17 批 2026Q1 consumer runs（按时间序）：

| run_id | 行业 | n_input | rejected | watch | data_missing |
|---|---|--:|--:|--:|--:|
| 20260711_111811_eadf_2026Q1 | 食品饮料（旧批） | 50 | 50 | 0 | 0 |
| 20260711_112114_e4a0_2026Q1 | 家电（旧批） | 50 | 47 | 0 | 3 |
| 20260711_112734_afd4_2026Q1 | 美护/个护（旧批） | 20 | 20 | 0 | 0 |
| 20260711_113042_b6e2_2026Q1 | 纺织服装（旧批） | 50 | 46 | 0 | 4 |
| 20260711_115033_0df5_2026Q1 | 服务消费批 1 | 50 | 46 | 0 | 4 |
| 20260711_115527_1f28_2026Q1 | 服务消费批 2 | 50 | 49 | 0 | 1 |
| 20260711_115558_4412_2026Q1 | 服务消费批 3 | 18 | 17 | 0 | 1 |
| 20260711_115712_1e2c_2026Q1 | 轻工制造批 1 | 50 | 48 | 0 | 2 |
| 20260711_115819_b9e4_2026Q1 | 轻工制造批 2 | 50 | 50 | 0 | 0 |
| 20260711_115931_8ac9_2026Q1 | 轻工制造批 3 | 45 | 44 | 0 | 1 |
| 20260711_120040_4c10_2026Q1 | 商贸零售批 1 | 50 | 46 | 0 | 4 |
| 20260711_120149_6bbe_2026Q1 | 商贸零售批 2 | 49 | 48 | 0 | 1 |
| 20260711_120207_f56a_2026Q1 | 纺织服装补尾 | 50 | 46 | 0 | 4 |
| 20260711_120244_2c6d_2026Q1 | 食品饮料补批 1 | 50 | 50 | 0 | 0 |
| 20260711_120409_599f_2026Q1 | 食品饮料补批 2 | 40 | 38 | 0 | 2 |
| 20260711_120522_df11_2026Q1 | 食品饮料补批 3 | 37 | 36 | 1（天味食品） | 0 |
| 20260711_120732_5e33_2026Q1 | 纺织服装补尾 2 | 41 | 37 | 0 | 4 |

合计 ~750 次 screening，仅 1 watch（天味食品 603317，`deducted_profit_proxy_used`），0 hit。所有 run 的 `coverage_json` 已写入 DuckDB。

### 1.4 本轮代码修复（对抗式 review 后）

修复了 `compute_pe_pb_percentile` 的一个 bug：当股票当前真实 PE 为负（亏损）但历史 PE 全为正时，原逻辑取过滤后最后一条正值作为 `current`，导致亏损股被错误归为 `pe_percentile_too_high`。

修复后效果（服务消费批 1 重筛对比）：
- 修复前：50 → 46 rejected（30 pe_percentile_too_high）+ 4 data_missing
- 修复后：50 → 29 rejected（15 pe_percentile_too_high）+ 21 data_missing（19 `pe_ttm_invalid` + 1 `pe_history_missing` + 1 `deducted_profit_missing`）
- 19 只（38%）从错误归因修正到 `pe_ttm_invalid`

新增的原因码（`src/screening/status.py`）：
- `data_missing_reason.pe_ttm_invalid` —— 已存在但首次启用，亏损股不再走 pe 分位
- `data_missing_reason.new_listing_insufficient_history` —— 上市不足 2 年且样本 <100，避免无限重试（001312 自动归此类）
- `watch_reason.deducted_profit_proxy_used` —— 扣非全缺走归母代理，从 `data_warning` 拆出独立原因码

新增回归测试：
- `tests/indicators/test_valuation.py`：5 条，覆盖负 PE / 极端值 / 样本不足 / 空 df
- `tests/strategies/test_consumer_stateful.py`：3 条新场景（负 PE、新上市、proxy 路径）+ 1 条修订（长跨度样本不足）

### 1.5 本轮代码验证

- 全套 `pytest -q`：427 passed, 1 skipped。
- `python3.9 -m compileall -q src scripts` 通过。
- 重试 4 只代码（000955 / 002875 / 300591 / 001312）：3 只去重后 2066 个日期已达标；001312 因 2026-04 上市客观不足，自动归为 `new_listing_insufficient_history`。

## 2. 未完成

### 2.1 P1：策略一数据覆盖

1. ✅ 5 个消费核心行业（食品饮料 / 家电 / 美护个护 / 纺织服装 / 服务消费 / 轻工制造 / 商贸零售）已全部预热完毕。
2. ✅ 4 只重试代码已处理。
3. ✅ 每批预热后用同一批 codes 跑 screen，run_id + coverage 已留痕。
4. ⏳ 待 P2 阶段结合人工标签、前瞻收益回测和下一期财务验证校准阈值；在此之前不拆分子行业阈值。

### 2.2 P1：策略二与策略三

1. 策略二A：用 `pharma-review` 找到 `positive_label_missed`，优先补集采事件的品种级证据和边界样本。
2. 策略三：按 `parser-review` 的 P1 队列修复 14 个 PDF 未解析海外收入样本和 ~21 条 `parse_warning`，先添加 golden case 再修改解析器。
3. 研报证据：无研报候选应降权或标记 `research_evidence_missing`，避免无证据候选进入高优先级队列。
4. 港股：将 `$global-stock-data` 的行情、K线、三表、新闻、资金流和 A/H 对照字段接入策略二B；明确保留 `hk_disclosure_source_gap`。

### 2.3 P2：验证与校准

1. 以策略一批次的拒绝分布与人工标签，评估 PE/PB、营收、毛利率和反转阈值。
2. 用 20/60/120 交易日前瞻收益和下一期财务验证，检查命中后的收益与业绩兑现。
3. 在候选级证据去重后，做轻量研报 claim 抽取，优先海外订单、产能、客户、License-out 与 FDA/CDE 进展。

## 3. 下一步命令

```bash
# 策略二A 漏标复盘
python3 -m src.pipeline.cli pharma-review --period 2025A

# 策略三解析器质量池
python3 -m src.pipeline.cli parser-review --year 2025

# 如需补跑某批（示例）
python3 -m src.pipeline.cli refresh --valuation-source akshare \
  --industries "休闲、生活及专业服务" --limit 50
python3 -m src.pipeline.cli screen --period 2026Q1 --strategy consumer \
  --codes <同一批代码>
```
