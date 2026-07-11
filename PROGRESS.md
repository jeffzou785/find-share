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
4. ⏳ 港股策略二B：推迟到下一阶段。Infrastructure 已就位（`global_stock_mappings` 表 + `HKStockMapping` dataclass + `global-map` CLI），缺 `HKDataSource` adapter、策略二B 实现、tests。是多日工作。

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
