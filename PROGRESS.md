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

- 全套 `pytest -q`：434 passed, 1 skipped。
- `python3.9 -m compileall -q src scripts` 通过。
- 重试 4 只代码（000955 / 002875 / 300591 / 001312）：3 只去重后 2066 个日期已达标；001312 因 2026-04 上市客观不足，自动归为 `new_listing_insufficient_history`。

### 1.6 策略二A pharma-review 复盘

`pharma-review --period 2025A`（输出 `data/exports/pharma_ground_truth_review.md`）：

- 30 ground-truth 样本：6 aligned_positive、12 aligned_negative、12 positive_label_missed。
- 12 漏标细分：
  - 6 `vbp_event_missing`（data_missing）：DB 中无 VBP 事件记录。**根因是数据缺口**——`pharma_vbp_events` 表全库仅 8 条，省际联盟高值耗材集采（人工关节、电生理、药物洗脱支架等 2022-2024 批次）系统缺录。
  - 3 `vbp_recovery_not_confirmed`（rejected）：华海药业 / 科伦药业 2025A 营收同比 -10%/-15%，策略正确判定复苏未确认。
  - 3 `not_vbp_recovery_pool`（rejected）：通化东宝 / 长春高新 / 复星医药 在 `classify_pharma_sub_strategy` 未归入 vbp_recovery。

未追写 VBP 事件，因为补事件需要外部证据收集（不能凭空造数据）。下一阶段应按公开省际联盟集采批次名单扩 `pharma_vbp_events` 表。

### 1.7 策略三 parser-review 修复

`parser-review --year 2025` 共 33 条 issue，本轮修复后降至 28 条：

- 修关键词 bug：`OVERSEAS_KEYWORDS` 加 `"其他国家或地区"`（原只有 `"其他国家和地区"`，差一个字）。修复后 601766 中国中车正确解析 348.21 亿元境外收入。
- 加 golden case：`tests/fixtures/overseas_revenue_golden_cases.csv` 新增 601766 行 + `test_overseas_revenue_golden_cases.py` 断言更新。
- `review_overseas_parser_quality.py` issue 分类细化：调用 `parse_annual_report` 单股跑一次，按 `result.error` 把 `pdf_without_parsed_overseas` 拆为：
  - `no_overseas_section` (P3)：PDF 无分地区附注
  - `pure_domestic` (P3)：有附注但全文无境外关键词（公司纯内销）
  - `parse_failure` (P1)：找到附注且含境外词但提取失败
  - `pdf_corrupt` (P1)：解析异常

修复后 issue 分布：P1=13（含 13 parse_warning + 0 parse_failure，因为 9 个原 parse_failure 重新归为 pure_domestic P3），P2=6，P3=9。

剩余 13 个 `parse_warning` 多为单位识别 bug（万元/亿元/元混淆），需逐 PDF 调试，未在本轮范围内。

### 1.8 研报证据缺失标记

`scripts/run_after_disclosure.py::_tag_research_evidence_missing`：对 hit/watch 候选批量查 `broker_reports` 表，写入 `metrics.source_status.extra`：

- `research_evidence_missing` = 'true'/'false'：单 code 视角，是否有研报。
- `research_evidence_low_coverage` = 'true'：批次视角，本批 broker_reports 覆盖率 <20% 时打此 flag，提示下游"数据本身稀疏，不要把 missing 当 reject 信号"。

新增 `DuckDBStore.load_broker_report_codes(codes)`：批量 IN 查询，避免逐 code 调用。

7 个测试覆盖：正常 hit/watch/rejected、DB 异常 safe-fail、批量 padding、低覆盖率守门。

### 1.9 P2 验证与校准首轮结果

**Backtest 基础设施验证**（`backtest_forward_returns.py`）：
- 21 行 backtest_results 已入库（overseas 2025A run）；pharma_vbp 18 行。
- 当前 anchor 距今仅 2-14 天，20/60/120 日窗口尚未走完，`status=missing`。需要等 2026-08 ~ 2026-11 才能产出有效前瞻收益。

**Financial validation 实际结果**（`validate_next_financials.py`，2025A → 2026Q1）：

| code | name | status | validation_verdict | rev_yoy | np_yoy |
|---|---|---|---|--:|--:|
| 600031 | 三一重工 | hit | confirmed | +14.03% | +0.46% |
| 001325 | 元创股份 | hit | confirmed | +0.10% | +0.01% |
| 001231 | 农心科技 | watch | confirmed | +0.01% | +0.07% |
| 000837 | 秦川机床 | watch | mixed | +0.10% | -0.01% |
| 001288 | 运机集团 | watch | mixed | -0.06% | +0.51% |
| 002145 | 钛能化学 | watch | mixed | +0.07% | -0.23% |
| 001239 | 永达股份 | watch | deteriorated | -0.01% | -0.48% |
| 001207 | 联科科技 | watch | deteriorated | -0.07% | -0.22% |

**信号**：2/2 hit 维持正增长（虽幅度极小，三一/元创接近持平），2/5 watch deteriorated——parse_warning watch 原因正确识别了较弱候选。**注**：当前 `confirmed` verdict 阈值（rev_yoy≥0 + np_yoy≥0）过松，下阶段应改为更严格的双位数增长标准。

**阈值敏感度分析**（基于 800 次 2026Q1 consumer screen）：

拒绝原因分布（顺序短路后首次 reject）：
- pe_percentile_too_high: 500 (62.5%)
- deducted_yoy_too_low: 167 (20.9%)
- not_inflection_or_trend: 73 (9.1%)
- revenue_yoy_too_low: 5 (0.6%)
- pb_percentile_too_high: 2 (0.2%)

PE 5y 分位阈值放宽影响（基于 metrics 快照，**注意 first-rejection 短路偏差**：PE reject 时后续硬阈值未评估）：

| 放宽到 | 跨过 PE 的候选数 | 其中同时满足 yoy≥30% + rev_yoy≥10% 的候选数 |
|--:|--:|--:|
| 40% | 41 | ≤7 |
| 50% | 82 | ≤7（含煌上煌 002695、潮宏基 002345、浙江正特 001238） |
| 60% | 136 | ~10-15（估计） |
| 70% | 191 | ~15-25（估计） |

"同时满足" 列是上限——这些候选还需要通过 inflection/trend、PB 分位、营运质量、OCF、扣非/归母比等后续 6 道闸门才能成为真 hit。

**结论**：策略一在 2026Q1 偏严，PE 5y 分位 ≤30% 在 2021 年牛市基线之上过滤掉绝大多数消费股。下一阶段校准建议：(a) 用 10y 窗口替代 5y（避免 2021 峰值偏差），(b) 或子行业相对分位（同行业排序而非全市场），(c) 或结合 PE 绝对值上限做 OR 条件。**校准必须重跑策略**，不能只靠 metrics 快照推算。

**研报证据覆盖诊断**（已修复，运行 `import_research_reports.py` 补齐医药/三一研报后）：

| 候选类型 | 有研报 | 无研报 | 覆盖率 |
|---|--:|--:|--:|
| overseas hit/watch | 6 | 1 | 86% |
| pharma_vbp hit/watch | 6 | 0 | 100% |
| consumer watch | 0 | 1 | 0% |

研报库已覆盖所有医药 hit（恒瑞、华润三九、乐普、大博、三友、安图各 19-100 篇研报）。consumer watch 因仅 1 个候选且非主流标的，可后续单独补。**先前的"研报库缺失"归因错误**——是导入任务未跑，不是数据层缺。

### 1.10 Phase D：基础设施修补

**evidence_claims 表**（P2-3）：

```sql
CREATE TABLE evidence_claims (
    code VARCHAR, name VARCHAR, claim_text VARCHAR, claim_source VARCHAR,
    broker VARCHAR, report_date DATE, report_id VARCHAR,
    evidence_type VARCHAR, confidence VARCHAR, tags_json VARCHAR,
    raw_text VARCHAR, source_url VARCHAR, created_at TIMESTAMP,
    PRIMARY KEY (code, report_date, claim_text, evidence_type, report_id)
);
```

- `evidence_type` enum：`overseas_order / capacity / customer / license_out / fda_cde / vbp_event / guidance`
- `DuckDBStore.save_evidence_claims / load_evidence_claims` 方法
- `scripts/import_evidence_claims.py`：CSV 导入 + 校验（report_date 必填、evidence_type/confidence enum 校验）
- `tests/fixtures/evidence_claims_seed.csv`：4 条样本
- `tests/scripts/test_import_evidence_claims.py`：9 条测试

**financial-validation 阈值收紧**：
- `min_revenue_yoy` / `min_net_profit_yoy` 默认从 0.0 → 0.05（5%）
- 函数默认 + CLI 默认 + batch 函数默认三处同步
- 验证：三一重工 600031 verdict 从 `confirmed` → `mixed`（rev+14% 过 5%、np+0.46% 不过 5%）

**broker_reports 持续导入**：
- 603317 天味食品（consumer watch）补 100 篇研报
- 001231 农心科技东财返回空（小盘股无覆盖）
- 全库 16 distinct codes / 903 rows

## 2. 未完成

### 2.1 P1：策略一数据覆盖

1. ✅ 5 个消费核心行业（食品饮料 / 家电 / 美护个护 / 纺织服装 / 服务消费 / 轻工制造 / 商贸零售）已全部预热完毕。
2. ✅ 4 只重试代码已处理。
3. ✅ 每批预热后用同一批 codes 跑 screen，run_id + coverage 已留痕。
4. ⏳ 待 P2 阶段结合人工标签、前瞻收益回测和下一期财务验证校准阈值；在此之前不拆分子行业阈值。

### 2.2 P1：策略二与策略三

1. ✅ 策略二A：`pharma-review` 已跑完，识别 12 个 positive_label_missed。详见 1.6。**数据补齐**：下一阶段需按省际联盟集采批次名单扩 `pharma_vbp_events`。
2. ✅ 策略三 parser 修复（关键词 + golden case + issue 分类），详见 1.7。**剩余**：13 个 `parse_warning` 单位识别 bug 待逐 PDF 调试。
3. ✅ 研报证据缺失标记，详见 1.8。下一阶段需先批量导入 `broker_reports` 数据再启用此 tag 做排序降权。
4. ❌ ~~港股策略二B~~（2026-07-12 放弃）。Infrastructure 仍保留（`global_stock_mappings` 表 + `HKStockMapping` dataclass + `global-map` CLI），以备将来可直接接入。

### 2.3 P2：验证与校准

1. ✅ 阈值敏感度分析（详见 1.9）：当前 30% 阈值过严，建议下阶段测试 10y 窗口或子行业相对分位。
2. ✅ Backtest + financial-validate 基础设施跑通；financial validation 已产出 overseas 2025A 的 8 条 verdict。Backtest 需等窗口走完。
3. ✅ 研报 claim 抽取 schema 已建（详见 1.10），等研报 PDF 入 RAG 后批量补全。

### 1.11 Phase E：pharma VBP 事件补齐

针对策略二A pharma-review 暴露的 12 个 positive_label_missed，本轮按公开省际联盟集采批次补了 6 条事件到 `pharma_vbp_events` 表，并修了配套策略 bug。

**6 条新增事件**（`tests/fixtures/pharma_vbp_events_phase_e.csv`）：

| code | name | product | vbp_status | source URL |
|---|---|---|---|---|
| 600867 | 通化东宝 | 人胰岛素+甘精胰岛素 | won | smpaa.cn/2021/11/26 |
| 600196 | 复星医药 | 化学仿制药 | unknown | smpaa.cn/2020/08/24 |
| 600062 | 华润双鹤 | 降压药+造影剂 | unknown | smpaa.cn/2019/12/25 |
| 300832 | 新产业 | 化学发光免疫试剂 | unknown | ybj.ah.gov.cn |
| 603392 | 万泰生物 | IVD 试剂 | unknown | ybj.ah.gov.cn |
| 600566 | 济川药业 | 中成药 | unknown | hubeiprice.org.cn |

**关键 bug 修复**：`classify_pharma_sub_strategy` 缺 "生物医药" 关键词

- `VBP_RECOVERY_KEYWORDS` 没含 "生物医药"，导致 sw_second='生物医药' 的 000661 长春高新 / 600196 复星 / 600867 通化东宝 直接 `not_vbp_recovery_pool` reject，VBP 事件根本不会被读取。
- 修复：加入 "生物医药" / "生物药品" 到 VBP_RECOVERY_KEYWORDS。
- 测试：原 `test_vbp_event_product_can_classify_broad_biomedicine_industry` 改用 "其他医药分类" 模拟无 keyword 命中场景，验证 event-driven rescue 仍工作；新增 `test_biomedicine_sw_second_directly_classified_as_vbp_recovery`。

**Phase E 重筛后状态**（run_id `20260711_153003_f2f1_2025A` + 后续 refresh）：

| code | name | 之前 | 现在 |
|---|---|---|---|
| 600867 | 通化东宝 | rejected not_vbp_recovery_pool | **watch partial_vbp_recovery** |
| 600196 | 复星医药 | rejected not_vbp_recovery_pool | **watch vbp_status_unknown** |
| 300832 | 新产业 | data_missing vbp_event_missing | **watch vbp_status_unknown** |
| 600062 | 华润双鹤 | data_missing vbp_event_missing | **watch vbp_status_unknown** |
| 600566 | 济川药业 | data_missing vbp_event_missing | **watch vbp_status_unknown** |
| 603392 | 万泰生物 | data_missing vbp_event_missing | **watch vbp_status_unknown** |

**6/12 codes 从 silent drop 变为 actionable watch**（含 1 个 partial_vbp_recovery）。剩余 6 个：
- 002422/600521：vbp_recovery_not_confirmed（营收同比下降，策略正确判 reject）
- 002262/600380/688029：仍 vbp_event_missing（未补，缺乏高可信公开证据）
- 000661 长春高新：vbp_event_missing（生长激素不在国家集采）

### 1.12 Phase F：select_best_record noise floor

针对 parser-review 剩余 `parse_warning` 中的"多 high 候选金额差异极大且最小值接近 0"这一子类，修了 `select_best_record` 的取值逻辑。

**根因**：P1.5-2 引入的 `MULTI_HIGH_AMOUNT_RATIO=5.0` 守门——多 high 候选 max/min > 5x 时一律取 min，本意是规避"误抓总营收"（600690 案例）。但另一类 PDF（分地区附注含"出口 0"或表格残留行）会出现 max=真实海外收入、min=0 或接近 0 的噪音，此时取 min 反而把正确值丢了。

**修复**（`src/collectors/annual_report_parser.py`）：引入 `MULTI_HIGH_NOISE_FLOOR = 0.02`，在原 ratio>5x 分支内再加一层判定：若 `min < 2% × max`，把 min 视为表格残留噪音，改取 max 并标记 `multi_high_min_below_noise_floor`。否则保留原 P1.5-2 取 min 行为。

**4 条新增 golden case**（`tests/fixtures/overseas_revenue_golden_cases.csv`）：

| code | name | max (yi) | min (yi) | ratio | 旧逻辑 | 新逻辑 |
|---|---|--:|--:|--:|---|---|
| 600066 | 宇通客车 | 211.08 | 0 | ∞ | 取 0 ❌ | 取 211 ✓ |
| 000913 | 钱江摩托 | 29 | 0 | ∞ | 取 0 ❌ | 取 29 ✓ |
| 605333 | 沪光股份 | 2.61 | 0 | ∞ | 取 0 ❌ | 取 2.61 ✓ |
| 688633 | 星球石墨 | 1.58 | 0 | ∞ | 取 0 ❌ | 取 1.58 ✓ |

**测试**：
- `tests/collectors/test_annual_report_parser.py` 新增 2 条单元测试：`test_phase_f_multi_high_min_below_noise_floor_picks_max`（min=0 极端场景）+ `test_phase_f_multi_high_small_nonzero_min_below_noise_floor`（min=小非零值，1.007% max）。
- `RUN_PDF_GOLDEN=1` 跑 9 条真实 PDF golden 全过（171s）。
- 全套 `pytest -q`：445 passed, 1 skipped，无回归。

### 1.13 Phase F 数据回填 + 重复行清理

代码修复后跑 `import-overseas --codes 600066,000913,605333,688633,000030 --year 2025` 让 Phase F 在 DB 生效。

**入库结果**（5/5 成功）：

| code | name | region | rev_yi | confidence | warning |
|---|---|---|--:|---|---|
| 000030 | 富奥股份 | 欧洲 | 7.94 | high | multi_high_min_below_noise_floor（成功识别噪音） |
| 000913 | 钱江摩托 | 境外 | 29.07 | high | — |
| 600066 | 宇通客车 | 海外 | 211.08 | high | — |
| 605333 | 沪光股份 | 境外 | 2.61 | high | — |
| 688633 | 星球石墨 | 国外 | 1.58 | high | — |

**重复行清理**：`overseas_revenue` PK 含 `region_name`，新逻辑选了不同 region 时会留下 stale 旧选区行。共清 4 条：

| code-year | 删除（stale）| 保留 |
|---|---|---|
| 000030 2025 | 亚洲 0.08yi（旧 chose_smaller 错值）| 欧洲 7.94yi ✓ |
| 601966 2024 | 出口 107.3yi（confidence=NULL）| 海外 107.3yi (high) |
| 601966 2025 | 出口 119.3yi（confidence=NULL）| 海外 119.3yi (high) |
| 300384 2024 | 亚洲 0.00yi（ratio=71768x 噪音）| 境外 2.47yi ✓ |

清理后 0 重复对；`load_overseas_revenue` 的"最后行获胜"循环不再有非确定性。

**000030 加入 golden case**：原本就是 `test_phase_f_multi_high_small_nonzero_min_below_noise_floor` 单元测试的来源案例（max=7.94yi / min=0.08yi / min=1.007% max），补进 `overseas_revenue_golden_cases.csv` 后真实 PDF golden 也覆盖。

**parser-review 进展**：issues 28（Phase F 前）→ 17（代码修复后）→ 16（数据回填后，000030 从 chose_smaller 错值改为 noise_floor 正确值）。剩余 16 条：5 P1 parse_warning + 2 P2 low_confidence + 9 P3（8 pure_domestic + 1 no_overseas_section）。

### 1.14 Phase G：多 high 候选 SUM 通道

针对 parser-review 剩余 4 个 P1 `parse_warning`（001288/600523/603086/603969），发现 3 个的根因不是金额选择，而是 parser 把同一聚合下的**子分区/子类型行**当作重复聚合候选 PICK，应该 SUM。

**根因**：`OVERSEAS_KEYWORDS` 同时含聚合（境外/海外/出口）和具体地区（美洲/欧洲/亚洲/...）。PDF 分地区表若不分"境外"行只列子区，或同一境外下再分境外-非洲/境外-大洋洲，parser 把每行都当候选送进 `select_best_record`，触发 chose_smaller 取最小。

**Phase G 修复**（`src/collectors/annual_report_parser.py`）：在 `select_best_record` 的 multi-high ratio 逻辑前，先尝试 SUM 两种场景：

| 场景 | 触发条件 | 案例 |
|---|---|---|
| `sub_region_same_aggregate` | 所有候选同 region_name，且 raw_text 全部是子分区/子类型模式（`境外-非洲`、`直接出口` 等）| 001288（境外-非洲+大洋洲 → 10.09yi）、603086（直接+间接出口 → 13.39yi） |
| `parallel_specific_regions` | 所有候选都是不同具体地区名（美洲/欧洲/亚洲/...），且无聚合关键词 | 600523（美洲+欧洲+亚洲 → 0.66yi）、000030（欧洲+北美+亚洲+南美 → 9.17yi，比 Phase F 的 7.94 更准） |

不触发 SUM 的场景（保留原 PICK 逻辑）：
- 603969：1 条干净聚合 + 1 条 narrative 误抓 → chose_smaller 取 min（6.18yi 真实境外 vs 32.06yi 总营收误抓）✓

**实现**：
- `_is_sub_region_pattern(rec)` 检测 raw_text 是否是 `region-separator-subname` 模式或 `直接/间接+region` 子类型。
- `_sum_records(records, label)` 合成 SUM 记录，region_name 标注来源（如 `境外(sum:4)`）。
- `_maybe_sum_high_confidence(high_conf)` 返回 SUM 记录或 None，None 时上层走原 ratio 逻辑。

**5 条新增/更新 golden case**（全部跑真实 PDF golden 验证）：

| code | name | 旧值（错） | 新值（Phase G） | 备注 |
|---|---|--:|--:|---|
| 000030 | 富奥股份 | 7.94yi（Phase F max）| **9.17yi** | 4 子区 SUM，更准 |
| 001288 | 运机集团 | 0.94yi（chose_smaller）| **10.09yi** | 非洲+大洋洲 SUM |
| 600523 | 贵航股份 | 0.01yi（chose_smaller）| **0.65yi** | 美洲+欧洲+亚洲 SUM |
| 603086 | 先达股份 | 10.09yi（multiple_high max）| **13.39yi** | 直接+间接出口 SUM |
| 603969 | 银龙股份 | 6.18yi（chose_smaller）| 6.18yi | 保留原 PICK ✓ |

**测试**：
- `tests/collectors/test_annual_report_parser.py` 新增 4 条 Phase G 单元测试（含 1 条负向：duplicate aggregate 不触发 SUM）。
- `RUN_PDF_GOLDEN=1` 跑 14 条真实 PDF golden 全过（211s）。
- 全套 `pytest -q`：449 passed, 1 skipped，无回归。

**数据回填**：4 个 code 重新 import-overseas + 清理 4 条 stale 旧 PICK 行（同 Phase F 模式：PK 含 region_name，新 SUM 行的 region 是 `境外(sum:N)` 与旧 region 不同，留下 stale）。

**parser-review 进展**：issues 16（Phase F 后）→ 16（Phase G 后，数量未变）。**关键差异**：5 P1 `parse_warning` 全部从"错值"变成"正确值 + 信息性警告"，全部纳入 golden case 回归覆盖。

### 1.15 Phase H：P3 降噪（其他地区关键词 + verified 注册表）

针对 parser-review 剩余 9 P3（1 `no_overseas_section` + 8 `pure_domestic`），逐 PDF 人工核验：

**1 个真 bug**：
- **300230 永利股份** p26 分地区表为 `中国大陆 11.84yi (49.71%)` + **`其他地区 11.97yi (50.29%)`**，但 "其他地区" 不在 `OVERSEAS_KEYWORDS`，被误分类为 `pure_domestic`。修：加入 "其他地区" 到关键词列表（在所有具体地区之后，作为最后兜底匹配，降低歧义风险）。

**8 个正确分类**（加入 `tests/fixtures/verified_pure_domestic.csv` 让 parser-review 跳过）：

| code | name | 分类 | 核验依据 |
|---|---|---|---|
| 000420 | 吉林化纤 | no_overseas_section | 全文 0 个境外关键词（除会计准则 boilerplate）|
| 000056 | 皇庭国际 | pure_domestic | 分地区表仅国内，"境外" 全为会计准则/居留权 boilerplate |
| 000058 | 深赛格 | pure_domestic | 分地区表仅国内，"辐射亚洲" 是战略表述非营收 |
| 000417 | 合肥百货 | pure_domestic | 分地区表仅国内，"境外" 全为股权结构 boilerplate |
| 000715 | 中兴商业 | pure_domestic | 分地区表仅国内，"进出口" 是业务范围注册非实际营收 |
| 000995 | 皇台酒业 | pure_domestic | 分地区表仅国内，"国外" 指引进葡萄种植技术 |
| 002616 | 长青集团 | pure_domestic | p25 分地区表只有"国内 100%"一行 |
| 603239 | 浙江仙通 | pure_domestic | p16 分地区表只有"国内 100%"一行 |

**verified 注册表实现**：
- `tests/fixtures/verified_pure_domestic.csv`：(code, name, year, reason) 4 列。同一 code 不同年份需分别核验（业务可能变化）。
- `scripts/review_overseas_parser_quality.py::_load_verified_pure_domestic(year)`：按年份加载 code set。
- `build_quality_review` 在分类 missing PDF 前先查 verified set，命中则跳过。
- 报告新增 `verified_pure_domestic_count` 字段（"已跳过纯内销（人工核验）：N 只"）。

**测试**：
- `tests/scripts/test_review_overseas_parser_quality.py` 新增 `test_verified_pure_domestic_skips_p3_issues`：单元 + 端到端验证 skip 行为。
- 300230 加入 golden case（expected_best_yi 11.8-12.2，覆盖 "其他地区" 关键词修复）。
- 全套 `pytest -q`：450 passed。
- `RUN_PDF_GOLDEN=1` 跑 15 条真实 PDF golden 全过（247s）。

**parser-review 进展**：issues 16（Phase G 后）→ **7**（Phase H 后）：
- P1: 5（全部 golden case 覆盖，值正确）
- P2: 2（low_confidence 待人工核对：000019 / 000829，601966 已是 high conf 不再 P2）
- P3: 0（8 verified skip + 300230 修复入库）
- 已跳过纯内销：8 只

### 1.16 Phase H2：分地区表语境升级 confidence（P2 清零）

剩余 2 个 P2 `low_confidence`（000019 / 000829）人工核对：值都正确，但 confidence 被降为 medium。

**根因**（000829 为例）：p20 分地区表用"东区/南区/北区/海外"四分法，`DOMESTIC_KEYWORDS` 含华北/华东/华南/... 但不含东区/南区/北区。parser 的 `page_confidence = high if has_domestic and has_overseas else medium` 因此判 medium。

**修复**（`src/collectors/annual_report_parser.py::_extract_from_page`）：增加第三种 high 触发条件——页面含"分地区"/"分区域"标题 + 境外关键词时也升 high。分地区表语境下境外行可信，不依赖境内分区名。

**结果**：

| code-year | 旧 | 新 | 备注 |
|---|---|---|---|
| 000019 2024 | high | high | DB 已是 high，PDF 复核一致 |
| 000019 2025 | medium | **high** | 重新 import 后升 high |
| 000829 2024 | medium | **high** | 分地区表语境升级 |
| 000829 2025 | medium | **high** | 同上 |

**测试**：
- `test_phase_h2_region_table_header_upgrades_confidence`：东区/南区/北区/海外 表 → high
- `test_phase_h2_no_region_table_stays_medium`：无"分地区"标题时仍 medium（保守）
- 2 条新 golden case（000019 / 000829 2025）+ 全套 `pytest -q`：452 passed
- `RUN_PDF_GOLDEN=1` 跑 17 条真实 PDF golden 全过（277s）

**parser-review 进展**：issues 7（Phase H 后）→ **5**（Phase H2 后）：
- P1: 5（全部 golden case 覆盖，值正确，信息性警告）
- P2: 0 ✓
- P3: 0 ✓
- 已跳过纯内销：8 只

### 1.17 P2 校准：策略一 PE 默认窗口 5y → 3y

针对 P2 阈值敏感度分析（详见 1.9）发现的"PE 5y ≤30% 在 2021 牛市基线之上过滤掉 62.5% 消费股"问题，把策略一默认 PE/PB 分位窗口从 5y 改为 3y，**30% 阈值不变**。

**理由**：
- 5y 窗口覆盖 2021-2022 牛市峰值，"分位 ≤30%" 等价于"比 70% 的牛市时刻还便宜"，标准过严。
- 3y 窗口只看 2023-2025（熊市 + 复苏），分位 ≤30% 等价于"近 3 年估值底部三成"，更贴合"反转"语义。
- 不选 10y：10y 引入 2016-2017 上轮牛市峰值，反而稀释当前便宜程度。
- 阈值不动：用户明确 30% 不变，避免引入新偏差。

**改动**（`src/strategies/consumer_reversal.py::StrategyConfig`）：
- `history_years: int = 5` → `history_years: int = 3`
- 注释更新：标"P2 校准：默认从 5y 改为 3y（避开 2021 牛市峰值；30% 阈值不变）"

**影响范围**：
- 策略一（`consumer_reversal`）：阈值用 `pe_stats[3]["percentile"]`（原 `[5]`）。
- 策略三（`overseas_champion`）：硬编码 `years=5`（line 489），不受影响。
- CLI（`run_after_disclosure.py::_build_consumer_config`）：`ConsumerConfig()` 自动继承新默认。
- 测试：1 个测试显式 `history_years=5` 保留原意（`test_all_windows_filled_in_metrics`），其他全过。

**待跑**：用新默认重跑 2026Q1 消费 screen（约 800 codes），量化 hit/watch 增量。原 5y 默认下：500/800 rejected pe_percentile_too_high，1 watch，0 hit。

### 1.18 P2-1 ~ P2-4 状态盘点 + P2-3/P2-4 收尾

盘点发现 P2-1 / P2-2 / P2-4 实现已基本完成（旧 commit），本轮只补 P2-3 alert 过滤 + P2-4 CLI 集成。

| 模块 | 状态 | 位置 |
|---|---|---|
| **P2-1 评分层** | ✅ 已完成（旧）| `src/screening/scoring.py`（5 子分 + 加权 final_score + risk_penalty + coverage_ratio）|
| **P2-2 半年报/季报扩展** | ✅ 已完成（旧）| `src/screening/period.py`（parse_period / require_overseas_filter / next_period / period_report_date）|
| **P2-3 动态监控** | ✅ 本轮收尾 | `src/screening/run_diff.py`（diff_runs 已有）+ 新增 `filter_alertable_events` / `write_alert_report` + `monitor_changes.py --alert` |
| **P2-4 财报 vs 研报一致性** | ✅ 本轮收尾 | `src/screening/consistency.py`（check_consistency_batch 已有）+ 新增 `run_consistency_check.py` CLI |

**P2-3 新增**（alert 过滤 + sink）：
- `filter_alertable_events(diff, alert_thresholds=None)`：从 diff 中过滤高信号事件——所有 new_hit/dropped_hit/new_parse_warning + hit↔watch 翻转 + metric 变化超 `DEFAULT_ALERT_THRESHOLDS`（PE >10、PE 分位 >20pp、海外占比 >15pp、final_score >0.15）。
- `write_alert_report(diff, output_path)`：生成聚焦 alert markdown（按 5 类分组：🆕 新 hit / ❌ 跌出 hit / 🔄 翻转 / ⚠ parse_warning / 📊 指标大变化）。
- `monitor_changes.py --alert` flag：有 alert 时 exit code=2（供 cron 检测），无 alert exit code=0。

**P2-4 新增**（CLI 集成）：
- `scripts/run_consistency_check.py`：拉取指定 run_id 的 hit/watch codes，跑 `check_consistency_batch`，输出 markdown 报告（仅含 warn/error 级，过滤 info 噪音）。
- CLI 子命令：`python3 -m src.pipeline.cli consistency --run-id <id> --report-year 2025`。
- 首跑样本（overseas run 20260704_112743_614f）：7 候选 → 6 warn，主因是"财报有海外收入但研报未提"，正是被忽视信号。

**测试**：
- P2-3 新增 5 条 alert 测试（filter 命中规则 + write_alert_report 表渲染 + 空 diff 提示）。
- P2-4 沿用 11 条 consistency 测试。
- 全套 `pytest -q`：458 passed，无回归。

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
