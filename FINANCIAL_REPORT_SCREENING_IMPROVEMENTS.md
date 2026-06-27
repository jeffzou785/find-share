# find-share 财报披露后选股框架改进意见（最终版 v2）

## 1. 目标

把 `find-share` 从“手动运行多个筛选脚本”升级成“财报披露后自动刷新、筛选、解释、复盘”的研究系统。

当前项目保留两条主线：

- **策略一：消费股反转**  
  周期低位、估值低位、业绩高速恢复的消费股。

- **策略三：出海隐形冠军**  
  制造业中海外业务高增、质量尚可、但市场关注度不足的标的。

后续文档、脚本、表名、报告统一使用“策略一 / 策略三”。策略二（医药量价齐升）已放弃，不再沿用。

## 2. 总体判断

当前项目的核心问题不是没有选股逻辑，而是缺少三个工程化能力：

1. **披露后自动处理**：公司公布年报、半年报、季报后，系统能自动拉取、解析、刷新、筛选。
2. **筛选过程可复盘**：不仅输出命中 CSV，还要记录为什么命中、为什么剔除、哪些数据缺失。
3. **策略信号可逐步增强**：先把硬过滤解释清楚，再逐步引入评分、关注度、RAG 文本证据。

建议改造顺序：

```text
先修断点
→ 再定义状态和 schema
→ 再落库复盘
→ 再做最小事件驱动入口
→ 再补策略信号
→ 最后才做复杂评分和 RAG 升级
```

## 3. P0：优先落地

### 3.1 P0-1：修复年报 PDF 命名

当前问题：

- `CnInfoDownloader.download_report()` 可能生成 `{code}_{year}_annual.pdf`
- 现有历史文件和 `scripts/import_overseas_revenue.py` 使用 `{code}_{year}_annual_report.pdf`

最终建议：

- 年报 canonical 文件名统一为：

```text
{code}_{year}_annual_report.pdf
```

- 半年报、季报继续使用清晰格式：

```text
{code}_{year}_half_year.pdf
{code}_{year}_q1.pdf
{code}_{year}_q3.pdf
```

- 导入脚本可以兼容 `_annual.pdf`，但下载器主输出必须回到 `_annual_report.pdf`。

涉及文件：

- `src/collectors/cninfo_downloader.py`
- `scripts/import_overseas_revenue.py`

建议补测试：

- 下载器年报文件名测试
- 导入脚本兼容历史文件名测试

### 3.2 P0-2：新增筛选运行审计表

当前只输出：

```text
data/exports/target_pool.csv
data/exports/target_pool_overseas.csv
```

这不利于复盘。建议新增两张表。

#### 3.2.1 screen_runs

记录每次策略运行：

```text
run_id
strategy
period
report_type
started_at
finished_at
input_count
hit_count
watch_count
rejected_count
data_missing_count
error_count
config_json
status
error
```

建议：

- `run_id` 用时间戳 + 短随机串，例如 `20250626_230500_a1b2`。
- `status` 使用 `running / success / partial_success / failed`。
- `screen_runs` 永久保留，小表不需要清理。

#### 3.2.2 candidate_scores

第一阶段不要急着做复杂评分，先把状态和原因落库：

```text
run_id
code
name
strategy
period
status
hit_reason
reject_reason
data_missing_reason
metrics_json
created_at
```

`status` 建议使用：

```text
hit
watch
rejected
data_missing
error
```

建议：

- `candidate_scores` 建复合索引：`(strategy, period, status)`。
- 不回填历史数据，从首次启用日开始记录。
- 可按报告期滚动保留 2 年，或保留全量后定期归档。

### 3.3 P0-3：定义 `metrics_json` schema

`metrics_json` 必须先定义结构，否则半年后复盘会变成字段混乱的 JSON 仓库。

建议顶层分组：

```json
{
  "valuation": {
    "pe_ttm": null,
    "pe_pct_3y": null,
    "pe_pct_5y": null,
    "pb": null,
    "pb_pct_3y": null,
    "market_cap_yi": null
  },
  "growth": {
    "revenue_yoy": null,
    "revenue_ttm_yoy": null,
    "deducted_profit_yoy_ttm": null,
    "net_profit_yoy": null
  },
  "quality": {
    "gross_margin": null,
    "net_margin": null,
    "roe": null,
    "ocf_to_net_profit": null,
    "debt_ratio": null
  },
  "overseas": {
    "overseas_ratio": null,
    "overseas_yoy": null,
    "overseas_revenue_yi": null,
    "parse_warning": null
  },
  "catalyst": {
    "reports_count_90d": null,
    "hot_reason_count_30d": null,
    "news_count_30d": null
  },
  "source_status": {
    "financials": "ok",
    "valuation": "ok",
    "annual_pdf": "missing",
    "overseas_parser": "skipped"
  }
}
```

约定：

- 未计算写 `null`，不要写 `0`。
- 数据源失败写入 `source_status`，不要只写在日志里。
- 如果某字段来自估算或低可信解析，需要额外写 `parse_warning` 或 `data_warning`。

### 3.4 P0-4：定义 `config_json` schema

`screen_runs.config_json` 至少记录：

```json
{
  "framework_version": "0.1.0",
  "strategy": "overseas",
  "period": "2025A",
  "thresholds": {
    "pe_ttm_max": 25,
    "pe_percentile_max": 30,
    "deducted_yoy_min": 0.30,
    "overseas_ratio_min": 0.30,
    "cashflow_quality_min": 0.70,
    "debt_ratio_max": 0.60
  },
  "data_sources": {
    "valuation_history": "akshare.stock_value_em",
    "valuation_snapshot": "tencent_quote",
    "financials": "sina+akshare",
    "reports": "eastmoney",
    "announcements": "cninfo"
  },
  "runtime": {
    "max_workers": 4,
    "timeout_seconds": 30,
    "resume": false
  }
}
```

P2 评分层落地后，再追加评分权重。

### 3.5 P0-5：明确 `watch / rejected / data_missing / error` 语义

状态定义：

- `hit`：硬过滤通过，关键数据完整，进入正式候选。
- `watch`：主线逻辑有吸引力，但存在软缺陷或数据疑点，需要人工跟踪。
- `rejected`：硬风控或核心阈值失败。
- `data_missing`：必要数据缺失，当前不能判断。
- `error`：代码异常、写库失败、解析异常等需要修复的问题。

建议映射：

```text
硬风控短路 → rejected
ST / 退市 / 明显财务异常 / 海外收入解析失败且无法判断 → rejected

软缺陷 → watch
parse_warning / 一致预期缺失 / 阈值边界附近 / 研报覆盖不足 → watch

必要数据缺失 → data_missing
PDF 未下载 / 财务表为空 / PE 历史为空 → data_missing

程序异常 → error
解析抛异常 / DuckDB 写入失败 / 未处理字段漂移 → error
```

特别约定：

- `parse_warning` 不应直接触发 `rejected`，优先进入 `watch`，并写入 `metrics_json.overseas.parse_warning`。
- 策略三的一致预期是可选过滤，缺失时不应直接 `rejected`。

### 3.6 P0-6：结构化剔除原因

策略一建议原因码：

```text
not_target_consumer_industry
pe_history_missing
pe_percentile_too_high
deducted_profit_missing
deducted_yoy_too_low
not_inflection_or_trend
market_cap_too_small
st_or_delisting
```

策略三建议原因码：

```text
not_target_manufacturing_industry
overseas_revenue_missing
overseas_ratio_too_low
overseas_ratio_abnormal
overseas_yoy_abnormal
pe_ttm_too_high
cashflow_quality_failed
debt_ratio_too_high
financial_data_missing
```

建议增加 watch 原因码：

```text
parse_warning
consensus_missing
near_threshold
low_report_coverage
weak_price_confirmation
```

### 3.7 P0-7：最小版 `run_after_disclosure.py`

新增脚本：

```text
scripts/run_after_disclosure.py
```

第一版只跑通“年报场景”，不要一开始就覆盖所有报告类型和所有数据源。

建议支持：

```bash
python3 scripts/run_after_disclosure.py --period 2025A --codes 600031 601058
python3 scripts/run_after_disclosure.py --period 2025A --from-disclosures --limit 30
python3 scripts/run_after_disclosure.py --period 2025A --strategy consumer
python3 scripts/run_after_disclosure.py --period 2025A --strategy overseas
python3 scripts/run_after_disclosure.py --period 2025A --strategy all
python3 scripts/run_after_disclosure.py --period 2025A --resume
```

第一版流程：

```text
读取股票池
→ 确定本次处理 code 列表
→ 创建 screen_runs(status=running)
→ 下载年报 PDF
→ 解析海外收入
→ 刷新必要财务数据
→ 跑策略一 / 策略三
→ 写 candidate_scores
→ 更新 screen_runs(status=success/partial_success/failed)
→ 导出 CSV / Markdown
```

第一版必须具备：

- `--codes` 指定股票
- `--limit` 控制数量
- `--strategy consumer|overseas|all`
- `--resume` 断点续跑
- 单股失败不中断全局运行
- 每只股票记录状态和失败原因
- 可重复运行，不重复下载大文件

`--resume` 语义：

- 跳过 `status in (hit, watch, rejected)` 且本次配置未变化的股票。
- 重试 `status in (data_missing, error)`。
- 如果 `config_json` 改变，默认重新跑；除非显式指定 `--resume-strict=false`。

### 3.8 P0-8：披露表覆盖率与 fallback

`--from-disclosures` 的前提是 `disclosures` 表覆盖足够好。第一版必须显式检查覆盖率。

建议：

- 运行前输出当前 `disclosures` 覆盖情况：

```text
period=2025A
disclosures rows=xxx
actual_date non-null=yyy
coverage=zz%
```

- 如果覆盖不足，提供 fallback：

```text
1. --codes 手动指定
2. 从已有候选池筛一批股票处理
3. 主动刷新披露日历
```

- 覆盖不足不能静默跳过，应写入 `screen_runs.error` 或给出明确 warning。

### 3.9 P0-9：并发、超时和重试

第一版不追求极致速度，但需要有边界。

建议默认：

```text
max_workers=1
non_em_max_workers=2-3
single_request_timeout=30s
single_stock_timeout=5min
run_timeout=4h
retry_times=2
```

注意：

- 东财接口必须遵守 `em_get()` 串行限流，第一版不要全局并发打东财。
- 可并发的主要是非东财任务，例如 PDF 下载、PDF 本地解析、部分新浪请求，上限先控制在 2-3。
- 单股超时后写 `status=error` 或 `data_missing`，不中断全局。

粗略预期：

```text
30 只年报候选：适合作为第一版默认 limit
100 只以上：必须依赖 --resume、日志、状态落库和分批执行
```

### 3.10 P0-10：输出路径和报告结构

不要覆盖旧的 `target_pool.csv` 作为唯一结果。建议每次 run 独立目录：

```text
data/exports/runs/{run_id}/
  consumer_2025A.csv
  overseas_2025A.csv
  report.md
  rejected_reasons.csv
  data_missing.csv
```

Markdown 报告结构：

```text
标题：run_id / period / strategy
一、汇总
hit / watch / rejected / data_missing / error 计数

二、命中清单
核心指标 + hit_reason

三、Watch 清单
核心指标 + watch_reason + parse_warning

四、剔除原因分布
reject_reason 聚合统计

五、数据缺失清单
code / missing reason / suggested retry

六、错误清单
code / exception type / message
```

建议后续增加：

```bash
python3 scripts/review_run.py {run_id}
```

用于按 `run_id` 重新生成 Markdown 报告。

### 3.11 P0-11：表创建和迁移策略

新增表和扩列统一放在 `src/storage/duckdb_store.py` 的 schema 初始化逻辑中。

建议：

- `screen_runs`、`candidate_scores` 用 `CREATE TABLE IF NOT EXISTS`。
- `disclosures` 扩列使用迁移函数检查列是否存在，缺失则 `ALTER TABLE ADD COLUMN`。
- 老数据不回填 `screen_runs/candidate_scores`。

`disclosures` 建议扩列：

```text
report_type
pdf_path
ingested_at
status
error
```

DuckDB 迁移注意：

- 老库新增列默认 `NULL`。
- `report_type` 可从 `period` 推导部分历史值，例如 `2025A -> annual`。
- `pdf_path / ingested_at / status / error` 历史行可留空。

### 3.12 P0-12：补关键测试

当前 `tests/` 目录为空。优先补：

- 年报 PDF 文件名测试
- `annual_report_parser` 单位识别测试
- 扣非 TTM 计算测试
- 策略一 `is_inflection` / `is_trend` 测试
- 策略三海外收入同比异常过滤测试
- `candidate_scores` 状态和原因写入测试
- `metrics_json` schema 测试
- `--resume` 状态跳过/重试测试

尤其是海外收入解析，单位错不会报异常，但会直接污染选股结果。

## 4. P1：补充策略信号

### 4.1 P1-1：策略一增强

策略一当前核心是：

```text
消费行业 + PE 分位低 + 扣非 TTM 同比高 + 反转判定
```

建议增强为：

```text
消费行业 + 估值低位 + 扣非恢复 + 收入确认 + 毛利率/现金流验证
```

新增信号：

- PB 分位
- 股价相对 3 年高点回撤
- 营收 TTM 同比
- 毛利率同比 / 环比改善
- 净利率同比 / 环比改善
- 经营现金流 / 净利润
- ROE 改善

目标是减少“低基数导致扣非高增，但主业并未真正恢复”的假反转。

### 4.2 P1-2：策略三增强

策略三当前核心是：

```text
机械/汽车/化工 + 海外收入占比 + PE + 现金流 + 负债率
```

它还缺“被市场忽视”的证据。建议先补数据，不急着上评分。

新增字段：

```text
is_ai_related
hot_reason_count_30d
news_count_30d
reports_count_90d
relative_return_60d
neglect_evidence
```

数据源建议：

- 东财概念板块归属：判断是否属于 AI、算力、机器人、半导体等热门概念。
- 同花顺热点：统计近 N 日是否频繁出现在热门题材。
- 东财个股新闻：统计近期新闻数量。
- 东财研报数量：统计近 30/90 日研报覆盖度。
- 行业相对收益：近 20/60/120 日股价是否落后行业但基本面更强。

### 4.3 P1-3：海外收入解析增强

当前已知风险：

- 单位识别错误
- 跨页数字断字
- 多候选记录时误抓总营收

建议增强：

- 同一股票多年数据交叉校验单位。
- 与利润表总营收做 ratio 校验。
- 加上下文排除词，如“营业收入合计”“主营业务收入合计”“总计”。
- 保留所有候选记录到调试字段，不只保留最大值。
- 对异常值输出 `parse_warning`。

### 4.4 P1-4：数据源分层，而非激进替换

不要短期直接用腾讯财经替换 AkShare 历史估值。更稳妥的路线是分层：

| 数据 | 短期主源 | 辅助源 | 说明 |
|---|---|---|---|
| 历史 PE/PB | AkShare | 本地累积腾讯快照 | AkShare 暂时不可替代 |
| 当日估值快照 | 腾讯财经 | AkShare | 腾讯稳定且轻量 |
| 财务三表 | 新浪 | AkShare / mootdx | 新浪三表字段更全 |
| 年报 PDF | 巨潮 | mootdx F10 摘要 | 巨潮权威 |
| 研报 | 东财 | 同花顺一致预期 | 东财研报列表更完整 |
| 题材/热点 | 同花顺热点 | 东财概念 | 用于被忽视判断 |

### 4.5 P1-5：数据源切换 baseline 回归

任何数据源替换前，先做 baseline。

建议样本：

```text
30-50 只股票
覆盖消费、机械、汽车、化工、白马、小盘、亏损、次新
```

对照指标：

```text
pe_ttm
pb
market_cap
revenue
deducted_net_profit
gross_margin
ocf_net
debt_ratio
```

规则：

- 差异超过阈值的字段不得直接替换。
- 例如 PE 差异 > 5%、营收差异 > 1%、扣非净利缺失率过高，都进入“不可替换”清单。
- 数据源替换后跑回归测试，防止策略命中清单突变。

## 5. P2：确认有效后再做

### 5.1 P2-1：评分层

等 `candidate_scores` 已稳定记录状态和原因后，再设计评分。

可从简单线性模型开始：

```text
final_score =
  growth_score * 0.30
  + valuation_score * 0.20
  + quality_score * 0.20
  + catalyst_score * 0.15
  + neglect_score * 0.15
  - risk_penalty
```

约束：

- 权重必须写入配置。
- 每次运行的权重写进 `screen_runs.config_json`。
- 不用评分覆盖硬风控，例如 ST、明显财务异常、海外收入解析异常。
- 分数只用于排序和 watch 分层，不作为唯一买入理由。

### 5.2 P2-2：扩展半年报和季报

年报场景跑通后，再扩展：

- 一季报：重点看扣非 TTM、营收恢复、毛利率变化。
- 半年报：可以看经营现金流、区域结构变化。
- 三季报：验证全年趋势和市场预期差。

注意：

- 季报通常没有完整分地区收入附注，不能强行要求海外收入更新。
- 策略三海外收入仍以年报/半年报为主，季报更多看收入、利润、现金流、订单线索。

### 5.3 P2-3：RAG 升级

保持轻量优先。

升级顺序：

1. metadata 强过滤
2. 同义词词典
3. 固定抽取模板
4. 外部 embedding API
5. 确认收益明显后，再评估本地 embedding 模型

默认不建议把 PyTorch、本地 transformers、chromadb 作为主线依赖。

## 6. 暂不优先新增的表

### 6.1 financial_snapshots

短期不建议新增物理表。

原因：

- revenue、gross_margin、roe、ocf_net、debt_ratio 等大多能从 `financials` 和 `financials_full` 派生。
- 过早物理化会带来一致性问题。
- 当前复盘需求可以先通过 `candidate_scores.metrics_json` 满足。

后续如果确实需要横向分析，再将常用指标列化或建立 DuckDB VIEW。

### 6.2 report_events

短期优先扩展现有 `disclosures`，不急着新增 `report_events`。

如果后续要记录公告、业绩快报、业绩预告、问询函、修订稿等更广义事件，再单独设计 `report_events`。

## 7. 执行计划

本章只说明执行顺序、阶段拆分、交付物和验收节点。具体实现要求以前文第 3-5 章为准，避免在执行计划里重复展开 schema、原因码、策略指标和数据源细节。

### 7.1 总体节奏

建议按下面顺序推进：

```text
前置准备
→ P0 年报闭环
→ P1 数据源和信号增强
→ P2 评分、报告期扩展、RAG
```

时间粗估：

- 前置准备：0.5-1 天。
- P0 年报闭环：2-3 周。
- P1 信号增强：3-4 周。
- P2 后续增强：按评分层、报告期扩展、RAG 三条线分别排期。

执行约束：

- 旧脚本保持可运行，新增能力先旁路输出。
- 每一步都要能独立验收。
- 所有状态、原因、配置、关键指标都要能落库复盘。
- 不用评分、RAG 或文本证据覆盖硬风控和结构化财务指标。

### 7.2 前置阶段：环境、基线和回滚约束

对应章节：

- 3.12 P0-12：补关键测试。
- 4.5 P1-5：数据源切换 baseline 回归。

执行内容：

- 确认 DuckDB 文件路径、PDF 缓存目录、导出目录、`requirements.txt` 和 Python 版本。
- 确认测试框架使用 `pytest`，并建立最小 `tests/` 目录。
- 记录当前可运行脚本和输出文件，尤其是 `target_pool.csv`、`target_pool_overseas.csv`。
- 保存状态化改造前的 baseline 快照。

baseline 路径建议：

```text
data/baselines/pre_stateful_{date}/
  target_pool.csv
  target_pool_overseas.csv
  strategy1_result.md
  strategy3_result.md
```

回滚约束：

- schema 只做前向迁移，不做破坏性重建。
- 新增表使用 `CREATE TABLE IF NOT EXISTS`。
- 旧表扩列使用 `ALTER TABLE ADD COLUMN IF NOT EXISTS`。
- 不回填历史 `screen_runs / candidate_scores`。

### 7.3 第一阶段：P0 年报闭环

目标是在年报场景下跑通“披露后处理 → 策略筛选 → 状态落库 → CSV/Markdown 输出 → 可复盘”的完整链路。

#### 7.3.1 步骤 0：修复断点和建立测试基线

对应章节：

- 3.1 P0-1：修复年报 PDF 命名。
- 3.12 P0-12：补关键测试。

交付物：

- 年报 canonical 文件名统一为 `{code}_{year}_annual_report.pdf`。
- `scripts/import_overseas_revenue.py` 兼容历史 `_annual.pdf` 和 `_annual_report.pdf`。
- PDF 命名、策略一反转判断、策略三现有海外收入硬过滤测试。
- 状态化改造前的 baseline 快照。

验收标准：

- 存量 PDF 可以继续导入。
- 新下载年报使用统一文件名。
- 现有核心策略逻辑有测试保护。
- 后续步骤可以用 baseline 判断命中清单是否发生无解释漂移。

#### 7.3.2 步骤 1：建立审计表、schema 和迁移能力

对应章节：

- 3.2 P0-2：新增筛选运行审计表。
- 3.3 P0-3：定义 `metrics_json` schema。
- 3.4 P0-4：定义 `config_json` schema。
- 3.5 P0-5：明确状态语义。
- 3.11 P0-11：表创建和迁移策略。

交付物：

- `screen_runs` 和 `candidate_scores`。
- `disclosures` 扩列迁移。
- `create_screen_run / finish_screen_run / save_candidate_scores` 等写入方法。
- `metrics_json` 和 `config_json` schema 测试。

验收标准：

- 新库可以直接创建全部新表。
- 老库初始化时可以自动补齐 `disclosures` 新列。
- 单次运行和单只股票结果都能落库。
- 未计算指标写 `null`，不写伪造的 `0`。

#### 7.3.3 步骤 2a：定义状态模型和原因码

对应章节：

- 3.5 P0-5：明确状态语义。
- 3.6 P0-6：结构化剔除原因。

交付物：

- `ScreeningResult` 数据结构。
- `hit / watch / rejected / data_missing / error` 状态枚举。
- 策略一、策略三原因码映射。
- `watch` 原因码映射。

验收标准：

- 每个状态有清晰语义。
- 硬风控、软缺陷、数据缺失、程序异常不会混在一起。
- `parse_warning / consensus_missing / near_threshold` 等问题可以进入 `watch`。

#### 7.3.4 步骤 2b：策略一全量状态化

对应章节：

- 3.5 P0-5：明确状态语义。
- 3.6 P0-6：结构化剔除原因。
- 3.12 P0-12：补关键测试。

交付物：

- 策略一从“只输出命中”改为“输出所有候选状态”。
- 策略一关键指标写入 `metrics_json`。
- 策略一状态和原因测试。

验收标准：

- 每只进入策略一评估的股票都有最终状态。
- 每个剔除、缺失、错误都有原因。
- 命中清单相对 baseline 的差异可解释。

#### 7.3.5 步骤 2c：策略三全量状态化

对应章节：

- 3.5 P0-5：明确状态语义。
- 3.6 P0-6：结构化剔除原因。
- 3.12 P0-12：补关键测试。

交付物：

- 策略三从“只输出命中”改为“输出所有候选状态”。
- 海外占比、海外同比、现金流质量、负债率等指标写入 `metrics_json`。
- 策略三状态和原因测试。

验收标准：

- 海外收入缺失、比例异常、同比异常、现金流失败、负债率过高可以区分记录。
- `parse_warning` 优先进入 `watch`，不直接混入 `rejected`。
- 命中清单相对 baseline 的差异可解释。

#### 7.3.6 步骤 2d：baseline diff 验证

对应章节：

- 3.12 P0-12：补关键测试。
- 4.5 P1-5：数据源切换 baseline 回归。

交付物：

- 新旧命中清单 diff。
- 差异解释清单。
- 无法解释的漂移问题列表。

验收标准：

- 旧 `target_pool.csv` 与新状态化输出的 hit 清单差异可解释。
- 旧 `target_pool_overseas.csv` 与新状态化输出的 hit 清单差异可解释。
- 未解释差异修复前，不进入自动化入口。

#### 7.3.7 步骤 3：实现年报版 `run_after_disclosure.py`

对应章节：

- 3.7 P0-7：最小版 `run_after_disclosure.py`。
- 3.8 P0-8：披露表覆盖率与 fallback。
- 3.9 P0-9：并发、超时和重试。
- 3.10 P0-10：输出路径和报告结构。

交付物：

- `scripts/run_after_disclosure.py`。
- `--codes / --from-disclosures / --limit / --strategy / --resume`。
- `data/exports/runs/{run_id}/` 独立输出目录。
- CSV、Markdown 报告、剔除原因分布、数据缺失清单、错误清单。

验收标准：

- 可运行 `python3 scripts/run_after_disclosure.py --period 2025A --codes 600031 601058 --strategy all`。
- 可运行 `python3 scripts/run_after_disclosure.py --period 2025A --from-disclosures --limit 30 --strategy all`。
- 单股失败不影响全局运行。
- `--resume` 可以跳过已完成股票并重试 `data_missing / error`。
- 启动时能处理超时遗留的 `screen_runs(status='running')`。

并发约束：

- 默认 `max_workers=1`。
- 非东财任务可以设置 `non_em_max_workers=2-3`。
- 东财接口继续串行限流，不做全局并发冲击。

#### 7.3.8 步骤 4：P0 端到端验收

对应章节：

- 3.7 P0-7：最小版 `run_after_disclosure.py`。
- 3.8 P0-8：披露表覆盖率与 fallback。
- 3.9 P0-9：并发、超时和重试。
- 3.10 P0-10：输出路径和报告结构。
- 3.12 P0-12：补关键测试。

交付物：

- 一次 2-5 只股票的手工代码 run。
- 一次 30 只股票的 `--from-disclosures --limit 30 --strategy all` run。
- 一份可读的 `report.md`。
- 一份 P0 验收问题清单。

验收标准：

- 30 只样本运行不需要人工中途介入。
- 每只股票都有状态、原因和核心指标。
- Markdown 报告能回答为什么命中、为什么剔除、哪里缺数据。
- 人工抽查覆盖命中、watch、剔除、数据缺失、错误五类样本。

### 7.4 第二阶段：P1 信号增强

P1 不建议一次性全做，应拆成三组独立验收。新增信号先进入 `metrics_json` 和 Markdown 报告，不直接改变硬过滤结果。

#### 7.4.1 步骤 5a：数据源分层和 baseline

对应章节：

- 4.4 P1-4：数据源分层，而非激进替换。
- 4.5 P1-5：数据源切换 baseline 回归。

交付物：

- 30-50 只股票的数据源 baseline。
- 关键字段差异分布。
- 不可替换字段清单。

验收标准：

- 历史估值、当日估值、三表财务、年报 PDF、研报、题材热点各自有明确主源和辅助源。
- 数据源切换不会导致命中清单无解释突变。

#### 7.4.2 步骤 5b：海外收入解析增强

对应章节：

- 4.3 P1-3：海外收入解析增强。

交付物：

- 多年交叉校验。
- 总营收 ratio 校验。
- 上下文排除词。
- 候选记录保留和 `parse_warning`。

验收标准：

- 单位错误、跨页断字、误抓总营收等问题能被识别或预警。
- 异常解析优先进入 `watch`，并留在 `metrics_json.overseas.parse_warning`。

#### 7.4.3 步骤 5c：策略一和策略三新信号

对应章节：

- 4.1 P1-1：策略一增强。
- 4.2 P1-2：策略三增强。

交付物：

- 策略一新增 PB 分位、营收确认、毛利率、现金流、ROE 等验证信号。
- 策略三新增概念归属、热点、新闻、研报覆盖度、相对收益和 `neglect_evidence`。

验收标准：

- 策略一能减少“低基数高增但主业未恢复”的假反转。
- 策略三能给出“被市场忽视”的数据证据。
- 新信号引入后的命中变化可以用 baseline 和指标变化解释。

### 7.5 第三阶段：P2 后续增强

P2 的三个方向依赖关系不强，分开实施，谁先验证清楚谁先合入。

#### 7.5.1 步骤 6：评分层

对应章节：

- 5.1 P2-1：评分层。

前置条件：

- `candidate_scores` 已稳定记录多次样本运行。
- P1 新信号已经进入 `metrics_json`。
- 硬风控、数据缺失和解析异常的状态语义已经稳定。

交付物：

- 可配置评分函数。
- 评分权重配置。
- Markdown 报告中的评分拆解。

验收标准：

- 每个 `final_score` 都能拆回各分项。
- 权重写入 `screen_runs.config_json`。
- 评分只用于排序和 watch 分层，不覆盖硬风控。

#### 7.5.2 步骤 7a：半年报和季报扩展

对应章节：

- 5.2 P2-2：扩展半年报和季报。

交付物：

- `run_after_disclosure.py` 支持 `q1 / half_year / q3`。
- 半年报和季报指标补充。

验收标准：

- 一季报聚焦扣非 TTM、营收恢复、毛利率变化。
- 半年报聚焦现金流、区域结构变化和可能披露的海外收入。
- 三季报聚焦全年趋势和预期差。
- 季报不强行要求海外收入附注。

#### 7.5.3 步骤 7b：轻量 RAG 升级

对应章节：

- 5.3 P2-3：RAG 升级。

交付物：

- metadata 强过滤。
- 同义词词典。
- 固定抽取模板。
- 必要时接入外部 embedding API。

验收标准：

- RAG 输出能提供文本证据，但不覆盖结构化财务指标。
- 不默认引入 PyTorch、本地 transformers、chromadb。

## 8. 理想第一版使用方式

```bash
python3 scripts/run_after_disclosure.py --period 2025A --from-disclosures --limit 30 --strategy all
```

输出目录：

```text
data/exports/runs/{run_id}/
```

输出内容：

- 新处理公司列表
- 策略一命中、watch、剔除、数据不足、错误清单
- 策略三命中、watch、剔除、数据不足、错误清单
- 每只股票的关键指标
- 每只股票的命中或剔除原因
- `screen_runs` 和 `candidate_scores` 落库
- CSV / Markdown 报告

最终目标不是一次性筛出更多股票，而是形成一个可复盘、可解释、可逐步增强的财报季机会发现系统。
