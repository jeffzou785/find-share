# find-share 财报披露后选股框架改进意见（README 同步 + Review v5 吸收版 v4）

本文件按 `README.md` 中截至 2026-06-27 的实现状态重新整理，并吸收 `FINANCIAL_REPORT_SCREENING_REVIEW.md` v5 的复核意见。核心变化是：

- P0 不再作为待执行事项，已升级为“已完成的财报季闭环底座”。
- P1 不再整体作为待执行事项，已升级为“已完成基础增强 + P1.5 剩余增强”。
- 后续执行重点收敛到 P1.5 和 P2，避免 README 与改进文档继续出现“一个说已完成、一个说待执行”的冲突。

## 本轮落地（2026-06-28）

P1.5 全部 + P2-0 已落地，对应代码 + 测试如下：

| 项 | 主要文件 | 测试 |
|---|---|---|
| P1.5-1 财务/估值本地落库 | `src/collectors/cached_impl.py`（`LocalCachedSource`）+ `scripts/refresh_financials_and_valuation.py`（新） | `tests/collectors/test_cached_impl.py`（5 用例）|
| P1.5-2 海外收入困难样本 | `src/collectors/annual_report_parser.py`（多 high 取小值 + 区域名词扩展）+ `src/strategies/overseas_champion.py`（candidates_json ratio 校验） | `tests/collectors/test_annual_report_parser.py`（+2 用例）+ `tests/strategies/test_overseas_stateful.py`（+2 用例） |
| P1.5-3 被忽视证据链 | `src/collectors/neglect_evidence.py`（新，`NeglectEvidenceCollector`）+ 策略层接入 | `tests/collectors/test_neglect_evidence.py`（9 用例）|
| P1.5-4 行业覆盖+窗口参数化 | `src/strategies/consumer_reversal.py`（`SUPPORTED_HISTORY_WINDOWS = (3,5,10)` + TARGET_INDUSTRIES 扩展）+ `src/screening/schemas.py`（`ValuationMetrics` 多窗口字段） | `tests/strategies/test_consumer_stateful.py`（+6 用例）|
| P1.5-5 RAG 语义质量升级 | `src/knowledge/research_rag.py`（同义词词典 + `expand_query_synonyms`） | `tests/knowledge/test_synonym_expansion.py`（7 用例）|
| P1.5-6 legacy CSV 字段补全 | `src/strategies/overseas_champion.py`（`_result_to_legacy_dict` 9 字段）+ `src/screening/schemas.py`（metrics 扩字段） | `tests/strategies/test_overseas_champion.py`（+2 用例）|
| P2-0 统一 CLI | `src/pipeline/cli.py`（12 子命令） | `tests/scripts/test_cli.py`（4 用例）|

测试覆盖从 161 → 197（+36）。本文件下文仍是原始计划，保留作为历史记录与后续 P2-1/P2-2/P2-3 的入口。

## 1. 目标

把 `find-share` 从“手动运行多个筛选脚本”升级成“财报披露后自动刷新、筛选、解释、复盘”的研究系统。

当前项目保留两条主线：

- **策略一：消费股反转**  
  周期低位、估值低位、业绩高速恢复的消费股。

- **策略三：出海隐形冠军**  
  制造业中海外业务高增、质量尚可、但市场关注度不足的标的。

后续文档、脚本、表名、报告统一使用“策略一 / 策略三”。策略二（医药量价齐升）已放弃，不再沿用。

## 2. 当前状态

README 已经记录了大量 P0/P1 工作完成项，因此当前判断应从“缺少工程化能力”调整为：

1. **P0 财报季闭环已基本跑通**：状态化结果、审计表、`run_after_disclosure.py`、baseline diff、测试体系已经落地。
2. **P1 基础信号已部分落地**：策略一新增 PB/营收/毛利率验证，策略三新增研报覆盖度，海外收入解析和数据源 baseline 工具已经完成基础版本。
3. **剩余问题集中在 P1.5 和 P2**：被忽视证据还不完整，海外收入解析仍有困难样本，财务/估值本地落库未完成，评分层、半年报/季报、轻量 RAG 仍待做。

建议后续改造顺序：

```text
先修 P1.5 的真实数据缺口
→ 再补“被忽视”证据链
→ 再做本地财务/估值落库
→ 再做评分层
→ 最后扩展半年报、季报、监控和 RAG
```

## 3. 已完成能力

### 3.1 已实现的两大策略

| 策略 | 当前逻辑 | README 当前结果 |
|---|---|---|
| 策略一：消费股反转 | 消费行业 + PE 分位低位 + 扣非同比高增 + 反转判定 + PB/营收/毛利率验证 | 4 只 |
| 策略三：出海隐形冠军 | 制造业相关行业 + 海外收入占比 30%-95% + PE<25 + 现金流/净利>=0.7 + 资产负债率<60% + 研报覆盖度 | 15 只 + 50 只扩展池 |

### 3.2 P0：财报季闭环底座已完成

README 中已标记完成：

| P0 能力 | 当前状态 |
|---|---|
| 年报 PDF 命名 | 已修复为 `{code}_{year}_annual_report.pdf`，导入脚本兼容 legacy `_annual.pdf` |
| 状态化抽象 | 已有 `Status / ScreeningResult / MetricsSchema` |
| 审计表 | 已新增 `screen_runs / candidate_scores` |
| 配置和指标 schema | 已有 `metrics_json / config_json` 对应结构 |
| 事件驱动入口 | 已新增 `scripts/run_after_disclosure.py` |
| baseline diff | 已新增 `scripts/baseline_diff.py` |
| stale run 清理 | `cleanup_stale_screen_runs` 已实现并修复 DuckDB interval 写法 |
| 策略三旧 CSV 入口状态化修复 | 已修 `run_overseas_champion` 重复 `_evaluate_one` 覆盖问题，旧 CSV 入口现在也走状态化路径 |
| 策略三 Markdown 字段修复 | 已修状态化后 `revenue_yi` 缺失导致的 Markdown 格式化崩溃，并补回总营收字段 |
| 测试覆盖 | README 记录已有 161 个测试 |

P0 后续只保留维护项：

- 确保旧 CSV 入口和 `run_after_disclosure.py` 的结果口径一致。
- 每次 schema 调整继续走前向迁移，不做破坏性重建。
- `screen_runs` 永久保留，`candidate_scores` 后续按报告期归档即可。

### 3.3 P1：基础策略增强已部分完成

README 中已标记完成：

| P1 能力 | 当前状态 |
|---|---|
| P1-1 策略一新信号 | 已加入 PB 5 年分位、营收同比、毛利率同比改善；数据缺失降级 watch；已修 AkShare `revenue_yoy / gross_margin` 百分数单位转换 |
| P1-2 策略三被忽视信号 | 已加入 `reports_count_90d`，写入 `metrics.catalyst`，不改硬过滤 |
| P1-3 海外收入解析增强 | 已加入总营收/合计行标记、置信度、跨页修复、多年交叉校验、`candidates_json`、`parse_warning` |
| P1-5a 数据源 baseline 工具 | 已新增 `scripts/data_source_baseline.py` |

P1 当前不是全部完成，而是“基础版完成”。策略三“被忽视”的证据链仍只完成研报覆盖度，尚未形成完整的概念、热点、新闻、相对收益闭环。

## 4. 当前设计约束

### 4.1 状态语义

继续沿用 P0 状态语义：

| 状态 | 语义 |
|---|---|
| `hit` | 硬过滤通过，关键数据完整，进入正式候选 |
| `watch` | 主线逻辑有吸引力，但存在软缺陷或数据疑点 |
| `rejected` | 硬风控或核心阈值失败 |
| `data_missing` | 必要数据缺失，当前不能判断 |
| `error` | 代码异常、写库失败、解析异常等需要修复的问题 |

特别约定：

- `parse_warning` 优先进入 `watch`，不要直接混入 `rejected`。
- 一致预期、研报覆盖、热点覆盖属于软证据，缺失时不要直接剔除。
- ST、明显财务异常、海外收入解析失败且无法判断，仍应硬剔除。

### 4.2 数据源分层

短期继续保持分层，不做激进替换：

| 数据 | 短期主源 | 辅助源 | 说明 |
|---|---|---|---|
| 历史 PE/PB | AkShare | 本地累积腾讯快照 | AkShare 暂时不可替代 |
| 当日估值快照 | 腾讯财经 | AkShare | 腾讯稳定且轻量 |
| 财务三表 | 新浪 | AkShare / mootdx | 新浪三表字段更全 |
| 年报 PDF | 巨潮 | mootdx F10 摘要 | 巨潮权威 |
| 研报 | 东财 | 同花顺一致预期 | 东财研报列表更完整 |
| 题材/热点 | 同花顺热点 | 东财概念 | 用于“被忽视”判断 |

任何替换都要先跑 `scripts/data_source_baseline.py`，用 30-50 只样本对比关键字段差异。

### 4.3 暂不优先新增的表

短期仍不建议新增 `financial_snapshots` 和 `report_events`：

- `financial_snapshots` 的大部分字段可从 `financials / financials_full / candidate_scores.metrics_json` 派生。
- `report_events` 与现有 `disclosures` 职责有重叠，短期优先扩展 `disclosures`。

后续如果要记录公告、业绩快报、业绩预告、问询函、修订稿等更广义事件，再单独设计 `report_events`。

## 5. 剩余升级项：P1.5

P1.5 是当前最应该优先做的部分。它不是锦上添花，而是把 README 中“已完成的 P1 基础版”补成真正可用于财报季挖掘的增强版。

优先级调整：

```text
先做财务/估值本地落库
→ 再修海外收入困难样本
→ 再补策略三“被忽视”证据链
→ 再补策略一行业覆盖和参数化
→ 最后做 RAG 语义质量轻量升级
```

原因：

- 当前策略运行仍依赖实时 AkShare 拉取，财报季数据可能变化，影响复盘稳定性。
- 后续“被忽视”证据、海外解析修复、评分层都依赖稳定的本地财务和估值数据。
- 先把关键数据落库，才能让同一批候选的多次运行结果可对比。

### 5.1 P1.5-1：财务和估值本地落库

README 当前问题：

- `financials / pe_pb_history` 表仍为空。
- 策略运行时每次实时拉 AkShare。

建议动作：

- 增加刷新脚本，例如 `scripts/refresh_financials_and_valuation.py`。
- 将策略一、策略三运行所需的 PE/PB 历史和财务摘要优先读本地表。
- 本地缺失时再实时拉取，并把结果写回 DuckDB。
- 对 AkShare 失败或字段漂移写入 `source_status`。
- 用 `scripts/data_source_baseline.py` 对本地落库数据和实时数据做差异检查。

验收标准：

- 重跑策略时不必每次全量实时拉 AkShare。
- `pe_pb_history` 对核心候选有可复用历史数据。
- `financials` 对核心候选有最近报告期摘要数据。
- 本地数据和实时数据差异可通过 baseline 检查。

### 5.2 P1.5-2：修海外收入解析困难样本

README 已知问题：

- 仍有 12 份 PDF 找到附注但未提取到境外行。
- 多 high 候选时，`select_best_record` 当前倾向取最大金额，存在误抓总营收风险。
- 例子：`600690 max=1429 yi vs min=62 yi`。

建议动作：

- 为 12 份失败 PDF 建 fixture 或样本清单。
- 针对 pdfplumber 表格结构识别不到的场景，增加文本行 fallback。
- 对多 high 候选输出候选分布，不只输出最终值。
- ratio 校验放在策略层做，不放在 parser 层做：
  - parser 层 `annual_report_parser.py` 只负责抽取候选、置信度和 `candidates_json`。
  - 策略层 `overseas_champion.py` 已经同时拿到总营收和海外收入，适合从 `candidates_json` 里挑选 ratio 更合理的候选。
  - 现有 `overseas_ratio_max=0.95` 是兜底过滤，后续应升级为“候选优选 + 异常 warning”。

验收标准：

- 12 份失败 PDF 至少能解释失败原因，其中一部分能被 fallback 修复。
- 多 high 候选不再简单取最大值。
- `parse_warning` 能区分“可疑但可用”和“无法判断”。
- 策略三不会因为误抓总营收而错误命中。

### 5.3 P1.5-3：补全策略三“被忽视”证据链

当前已完成：

- `reports_count_90d` 已写入 `metrics.catalyst`。

仍待完成：

```text
is_ai_related
hot_reason_count_30d
news_count_30d
relative_return_60d
neglect_evidence
```

建议新增：

- 东财概念板块 collector：判断是否属于 AI、算力、机器人、半导体等热门概念。
- 同花顺热点 collector：统计近 30 日是否频繁出现在热门题材。
- 东财个股新闻 collector：统计近 30 日新闻数量。
- 行业相对收益计算：近 20/60/120 日是否落后行业。
- `neglect_evidence` 结构化字段：把低研报覆盖、低热点覆盖、低新闻覆盖、相对收益落后组合成一句可解释证据。

依赖关系：

- `is_ai_related / hot_reason_count_30d / news_count_30d / relative_return_60d` 可以分批交付，各自独立填入 `metrics_json`。
- `neglect_evidence` 是最后一步聚合字段，依赖前面这些数据源。
- 建议先做最容易拿的“研报覆盖度 + 新闻数”，再做热点、概念和相对收益。

验收标准：

- 策略三 Markdown 报告能解释“为什么说它被忽视”。
- 新增信号先进入 `metrics_json`，不要直接改变硬过滤结果。
- 对热门 AI/机器人/半导体标的能识别为“不够被忽视”或进入 watch。

### 5.4 P1.5-4：策略一行业覆盖和参数化

README 当前问题：

- “美容护理”覆盖不全。
- PE 分位时间窗口固定 5 年。

建议动作：

- 检查行业映射中美容护理、化妆品、个护、医美消费属性公司的覆盖。
- 建立策略一目标行业补充映射，不仅依赖单一 `sw_first`。
- 将 PE/PB 分位窗口参数化，例如 `3y / 5y / 10y`。
- 在 `screen_runs.config_json` 中记录窗口参数。

验收标准：

- 策略一不会因为行业映射名称差异漏掉典型消费标的。
- 不同估值窗口的结果可以复盘比较。

### 5.5 P1.5-5：RAG 语义质量轻量升级

README 当前问题：

- 研报 RAG 仍是 TF-IDF 关键词匹配。

继续保持轻量优先，不建议默认引入 PyTorch、本地 transformers、chromadb。

升级顺序：

1. metadata 强过滤。
2. 同义词词典。
3. 固定抽取模板。
4. 外部 embedding API。
5. 确认收益明显后，再评估本地 embedding 模型。

验收标准：

- 常见问法如“海外订单 / 出口 / 境外收入 / 一带一路 / 欧洲渠道”能互相召回。
- RAG 输出只作为文本证据，不覆盖结构化财务数据。

### 5.6 P1.5-6：补全 legacy CSV 字段

状态化改造后，`run_after_disclosure.py` 已经能输出完整状态和指标，但旧 CSV 入口仍要尽量保持信息量，避免用户从 `run_phase3_strategy3.py` 看到的字段比以前少。

已修复：

- `revenue_yi` / `total_revenue_yi` 已补回，避免 Markdown 格式化 `None:.1f` 报错。
- `run_phase3_strategy3.py` Markdown 输出已做数值字段防御格式化。

仍建议补齐：

```text
ocf_net_yi
net_profit_yi
total_liabilities_yi
total_assets_yi
eps_current
eps_forecast_y1
eps_forecast_y2
eps_y1_growth
eps_y2_growth
```

验收标准：

- `run_phase3_strategy3.py` 旧 CSV 和 Markdown 不崩。
- 旧 CSV 入口展示的财务质量、一致预期字段不低于状态化改造前的信息量。
- 缺失字段用空值或 `N/A` 展示，不因格式化失败中断。

### 5.7 P1.5 完成定义

跑一次 30 只样本：

```bash
python3 scripts/run_after_disclosure.py --period 2025A --from-disclosures --limit 30 --strategy all
```

P1.5 可以视为完成的标准：

- 总耗时小于 5 分钟，关键财务和估值数据主要从本地 DuckDB 读取，AkShare 仅作 fallback。
- Markdown 报告能解释每只股票的 `hit / watch / rejected / data_missing / error` 原因。
- 策略三 watch 清单中，`neglect_evidence` 字段对主要候选非空。
- 12 份 parser 失败 PDF 至少修复一半，剩余失败样本能解释原因。
- 多 high 候选海外收入不会简单取最大值，而是经过策略层 ratio 校验或给出明确 `parse_warning`。
- 策略一行业覆盖和估值窗口参数写入 `screen_runs.config_json`，可复盘比较。
- 旧 CSV/Markdown 入口保留关键财务质量和一致预期字段，不因状态化字段缺失而降级。

## 6. P2：后续增强（✅ 已完成 2026-06-28）

### 6.1 P2-0：统一 CLI ✅

`python3 -m src.pipeline.cli <subcommand>`，封装 13 个脚本（bootstrap/refresh/screen/strategy1/strategy3/reports/pdf/rag/baseline/monitor 等）。
旧脚本继续保留，CLI 只是统一入口；`screen` 子命令默认调用 `run_after_disclosure.py` 的状态化链路。

### 6.2 P2-1：评分层 ✅

`src/screening/scoring.py` 实现线性模型：

```text
final_score =
  growth_score * 0.30
  + valuation_score * 0.20
  + quality_score * 0.20
  + catalyst_score * 0.15
  + neglect_score * 0.15
  - risk_penalty
```

约束均已满足：
- 权重写入 `screen_runs.config_json.score_weights`（按策略差异化：consumer neglect=0，overseas neglect=0.20）
- 评分只用于排序和 watch 分层（不覆盖硬风控）
- 子分缺失时权重重新归一化（不强行惩罚缺数据）
- `run_after_disclosure.py --enable-scoring` 触发，命中清单按 `final_score` 降序排
- 27 个单元测试覆盖

### 6.3 P2-2：半年报和季报扩展 ✅

`src/screening/period.py` 提供 `parse_period(period) → PeriodInfo(kind, year, has_overseas_notes)`。
- 年报 / 半年报：完整海外收入硬过滤
- 季报（Q1/Q3）：跳过 `overseas_ratio` 硬过滤，仍跑 PE / cashflow / leverage；`overseas_revenue` 缺失不再判 `data_missing`
- `overseas_champion._evaluate_one_to_result` 增加 `overseas_required` 参数，由 `require_overseas_filter(period)` 控制

7 个新测试覆盖（季报场景下 overseas 缺失仍可走完评估、季报 PE 阈值仍生效、年报/半年报保持原行为等）。

### 6.4 P2-3：动态监控 ✅

`src/screening/run_diff.py` + `scripts/monitor_changes.py`：
- `diff_runs(before, after)` 输出 5 类事件：new_hit / dropped_hit / status_changed / metric_changed / new_parse_warning
- 关键指标阈值化 diff（PE 变化>5 / 研报数变化>3 / 海外占比变化>10pp / 综合评分变化>0.1）
- `diff_latest_two_runs(strategy, period)` 便捷方法
- CLI 子命令：`python3 -m src.pipeline.cli monitor --strategy overseas --period 2025A`
- 17 个单元测试覆盖

### 6.5 P2-4：财报 vs 研报一致性校验 ✅

`src/screening/consistency.py` 输出软证据（不改硬过滤）：
- 研报 EPS Y1 预测 vs 财报实际 EPS（偏差>25% → warn，否则 info）
- 财报海外收入 vs 研报标题关键词匹配：研报未提"海外/境外/出口" → warn（被忽视信号）
- 区域 / 订单 / 产能关键词命中 → info（一致证据）
- `check_consistency_batch` 一次性预加载 financials_full / broker_reports / overseas_revenue，避免 N+1
- 11 个单元测试覆盖

### 6.6 P2-5：Tushare 兜底 ✅（stub）

`src/collectors/tushare_impl.py` 提供 `TushareSource` stub：
- 锁定 `DataSource` Protocol 接口形状（7 方法）
- 无 token 时实例化允许，方法调用抛 `NotImplementedError` 并打印激活路径
- 业务代码已通过 Protocol 抽象，未来切 Tushare 时零修改
- 3 个单元测试覆盖（实例化、方法抛错、Protocol 形状校验）

P2 至此全部完成，测试覆盖从 197 → 271（+74）。

## 7. 当前推荐使用方式

### 7.1 财报季主入口

后续财报季优先使用状态化入口：

```bash
python3 scripts/run_after_disclosure.py --period 2025A --from-disclosures --limit 30 --strategy all
```

输出目录：

```text
data/exports/runs/{run_id}/
```

输出内容：

- 新处理公司列表。
- 策略一命中、watch、剔除、数据不足、错误清单。
- 策略三命中、watch、剔除、数据不足、错误清单。
- 每只股票的关键指标。
- 每只股票的命中或剔除原因。
- `screen_runs` 和 `candidate_scores` 落库。
- CSV / Markdown 报告。

### 7.2 兼容入口

旧脚本继续保留，适合单策略快速查看：

```bash
python3 scripts/run_phase2_strategy1.py
python3 scripts/run_phase3_strategy3.py
```

兼容关系：

- `run_phase3_strategy3.py` 已通过 `run_overseas_champion` 内部走状态化路径，行为应与 `run_after_disclosure.py --strategy overseas` 的策略评估口径一致。
- `run_phase2_strategy1.py` / `run_phase3_strategy3.py` 仍适合快速导出旧版 CSV/Markdown。
- 后续复盘、watch、data_missing、error、resume、run 级报告，都应以 `run_after_disclosure.py` 为准。

## 8. README 仍建议同步的地方

README 已经比旧 improvements 更接近代码现状，但仍有几处建议后续同步：

- “DuckDB 11 张表”应更新为 **13 张表**，新增表为 `screen_runs / candidate_scores`。
- “9 个可独立运行脚本”应更新为 **11 个脚本**，新增脚本包括 `run_after_disclosure.py`、`baseline_diff.py`、`data_source_baseline.py`。
- 快速开始可以补充 `run_after_disclosure.py` 作为财报季主入口。
- “研报量 >100 篇升级到 sentence-transformers + bge-small-zh”建议改成轻量路线：metadata、同义词、模板、外部 embedding API、本地 embedding 最后评估。

最终目标不是一次性筛出更多股票，而是形成一个可复盘、可解释、可逐步增强的财报季机会发现系统。
