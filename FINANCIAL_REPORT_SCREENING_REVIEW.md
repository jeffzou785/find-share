# find-share 财报披露改进文档 Review（v4）

针对 `FINANCIAL_REPORT_SCREENING_IMPROVEMENTS.md` 的复核意见。

## 文档状态

- v3 review 基于改进文档「最终版」（2026-06-26）。
- v3 相比 v2 的变化：
  - v2-2.1「评分层硬性风控短路」已被最终版 P2-1 采纳，移入「已采纳」。
  - 新增 4 条 v3 意见：watch 语义、`config_json` 结构、CSV/Markdown 输出、`disclosures` 扩列语义。
  - v2 待补充章节 5 条意见最终版仍未响应，保留并标注。
- v4 相比 v3 的变化：
  - 改进文档「最终版 v2」新增了「执行计划」章节（步骤 0-7）。
  - v4 新增「四、执行计划复核」小节，复核执行计划合理性，提出 5 个会踩坑的具体问题。
- 所有代码引用位置已于 2026-06-26 重新核查。

## 一、已采纳（最终版已吸收）

### 1.1 策略命名前后矛盾 ✓

- v1 意见：改进文档同时使用「策略二出海」和「策略三出海」，commit `94d3753` 显示策略二（医药量价齐升）已弃用。
- 核查：`src/strategies/overseas_champion.py:1` docstring 写「策略三：出海隐形冠军」。
- 最终版响应：第 15 行明确「后续文档、脚本、表名、报告统一使用策略一 / 策略三」。

### 1.2 RAG 升级路线违反轻量偏好 ✓

- v1 意见：升级到 `sentence-transformers + bge-small-zh` 会拉进 PyTorch / transformers / huggingface_hub + 模型权重，违反 memory `feedback_avoid_chromadb.md` 的轻量偏好。
- 最终版响应：P2-3 第 351-363 行保持轻量升级顺序（metadata → 同义词 → 关键词模板 → 外部 embedding API），明确「不建议把 PyTorch、本地 transformers、chromadb 作为主线依赖」。

### 1.3 数据源替换方案隐藏巨大成本 ✓

- v1 意见：腾讯财经无 PE/PB 历史，本地累积到能算 3 年分位至少要 3 年；AkShare 不应被替换，应分两条路。
- 最终版响应：P1-4 第 300-311 行数据源分层表明确「AkShare 暂时不可替代」「腾讯财经只补当日快照」。

### 1.4 新增表存在冗余和职责重叠 ✓

- v1 意见：`financial_snapshots` 字段都能从 `financials` + `financials_full` 派生；`report_events` 和已有 `disclosures` 表字段重合。
- 核查：`src/storage/duckdb_store.py:91` 确实已有 `disclosures` 表。
- 最终版响应：第 365-393 行明确「不建议新增 `financial_snapshots`」「优先扩展现有 `disclosures`，不急着新增 `report_events`」。

### 1.5 PDF 文件名 bug 结论偏了 ✓

- v1 意见：`cninfo_downloader.py:193` 写 `{code}_{year}_{report_type}.pdf` 会产出 `_annual.pdf`，但存量文件全是 `_annual_report.pdf`；正确做法是改下载器回到 `_annual_report.pdf`（方案 A），不是让 import 脚本去兼容。
- 核查：
  - `src/collectors/cninfo_downloader.py:193` 确实写 `f"{code}_{year}_{report_type}.pdf"`，默认 `report_type="annual"` → `_annual.pdf`。
  - `data/pdfs/annual_reports/` 目录下 20+ 份历史文件全部是 `_annual_report.pdf`。
- 最终版响应：P0-1 第 37-71 行明确「年报 canonical 文件名统一为 `{code}_{year}_annual_report.pdf`」，定性为 P0。

### 1.6 评分层硬性风控短路 ✓（v3 新确认采纳）

- v2 意见：评分层应明确「硬过滤命中（ST / 造假疑点 / 海外收入 parse_warning）应绕过 final_score 直接剔除，而非仅作为 risk_penalty 扣分」。
- 最终版响应：P2-1 第 335 行明确「不用评分覆盖硬风控，例如 ST、明显财务异常、海外收入解析异常」。
- 仍待补充的关联点见 3.6（watch 状态语义）。

## 二、部分采纳（被推迟或部分响应）

### 2.1 事件驱动入口的并发模型仍未定义

- v1 意见：`run_after_disclosure.py` 粗估 100 家披露需要数小时，改进文档没提并发模型、超时、断点续跑。
- 最终版响应：P0-4 第 205-212 行明确「单股失败不中断全局运行」「可重复运行，不重复下载大文件」。
- **未解决点**（v2 已提出，最终版仍未响应）：
  - **并发模型**：`_em_throttle.py` 是串行限流。100 家 ×（东财研报 + 同花顺一致预期 + 新浪三表 + PDF 下载）的总耗时估算和并发上限。
  - **超时与重试**：单只股票某数据源拉失败时，重试几次？超时多久？
  - **断点续跑**：跑到一半中断后，`screen_runs.status` 如何标记？重跑时如何跳过已成功项？
- 建议给一个粗估：单只 N 分钟 × 100 只 = 总耗时，并发上限 4-8，总超时 4h。

## 三、待补充（v2 延续 + v3 新增）

### 3.1 `metrics_json` schema 未定义（v2 延续，最终版仍未响应）

- 最终版 P0-2 第 119 行只有字段名 `metrics_json`，第 135 行只举了「PE 分位、扣非同比、海外收入占比、现金流质量」。半年后复盘时字段会乱。
- 待补充：至少约定顶层 key 分组：

  ```text
  metrics_json:
    valuation: { pe_ttm, pe_pct_3y, pb, market_cap }
    growth:    { revenue_yoy, deducted_profit_yoy_ttm }
    quality:   { gross_margin, roe, ocf_to_net_profit, debt_ratio }
    overseas:  { overseas_ratio, overseas_yoy, parse_warning }
    catalyst:  { reports_count_90d, hot_reason_count_30d }
  ```

- 同时约定：未计算写 `null` 不写 `0`，避免和真实 0 混淆。

### 3.2 `disclosures` 表覆盖率和 fallback（v2 延续，最终版仍未响应）

- 最终版 P0-4 第 186 行 `--from-disclosures` 默认此表覆盖好、更新及时，但没核验现状。
- 待补充：
  - 当前覆盖率（多少股票、多少报告期）和更新频率。
  - 缺失时 fallback：`--codes` 手动指定 / 全市场扫描 / 主动从东财披露日历补。
  - 流程跑不通时不应静默跳过，要落到 `screen_runs.error`。

### 3.3 失败语义和 `--resume`（v2 延续，最终版仍未响应）

- 最终版 P0-4 只说「单股失败不中断」「可重复运行」，没区分两类失败：
  - **数据缺失**（PDF 没下来、研报没覆盖）→ `status=data_missing`，可重跑。
  - **代码异常**（解析报错、DuckDB 写入失败）→ `status=error`，需人工。
- 待补充：
  - 单只失败时把入参（code、period、strategy）+ 异常类型 + 异常 message 落到 `candidate_scores`。
  - 提供 `--resume` 标志：重跑时跳过 `status in (hit, watch)`，只重试 `data_missing / error`。

### 3.4 表创建位置和迁移策略（v2 延续，最终版仍未响应）

- 最终版 P0-2 新增两张表，但没说：
  - `CREATE TABLE IF NOT EXISTS` 放哪？建议在 `src/storage/duckdb_store.py` 现有 schema 初始化函数里加，不要另写脚本。
  - 旧数据是否回填？建议**不回填**，从首次启用日开始。
  - 保留多久？建议 `screen_runs` 永久保留（小表），`candidate_scores` 按 period 滚动保留 2 年。
  - `candidate_scores` 复合索引：`(strategy, period, status)`，方便复盘查询。

### 3.5 数据源切换需要 baseline 回归测试（v2 延续，最终版仍未响应）

- 最终版 P1-4 数据源分层表涉及多处切换：新浪三表 vs AkShare 财务、腾讯快照 vs AkShare PE、巨潮 vs mootdx F10。
- 风险：字段映射（「营业总收入」vs「营业收入」、净利润口径、EPS TTM 算法）容易静默漂移。
- 待补充：
  - 切换前先建数值对照 baseline（30-50 只样本股 × 关键指标），记录两源差异分布。
  - 差异超阈值（如 PE 差 > 5%）的字段进入「不可替换」清单，继续用旧源。
  - 切换后跑回归测试，防止策略命中清单突变。

### 3.6 `watch` 状态和「软剔除」语义不清（v3 新增）

- 最终版 P0-2 第 125-131 行 `status` 枚举有 `watch`，但 P0-3 第 144-168 行的剔除原因全是 `rejected` 导向，没有「数据存疑但可关注」的中间态。
- 现实问题：海外收入 `parse_warning`、一致预期缺失这种「数据有疑点但不至于剔除」的情况，应走 watch，不应直接 rejected。
- 待补充：
  - 明确映射规则：硬风控短路（ST / 造假疑点 / 解析失败）→ `rejected`；可选数据缺失或阈值边界 → `watch`。
  - `parse_warning` 不应触发 rejected，应触发 watch 并在 `metrics_json.overseas.parse_warning` 留痕。
  - 策略三 `overseas_champion.py:8` 一致预期是「可选过滤」，缺失时应明确走 watch 而非 rejected。

### 3.7 `screen_runs.config_json` 结构未定义（v3 新增）

- 最终版 P0-2 第 100 行有 `config_json` 字段，但没说存什么。
- 待补充：建议至少存：
  - 策略阈值（PE 上限、扣非同比下限、海外收入占比下限、现金流质量下限、负债率上限）。
  - 数据源版本（AkShare / 新浪 / 腾讯 / 东财）。
  - 框架版本号（用于复盘时知道当时逻辑）。
  - P2 评分层落地后，再追加评分权重。

### 3.8 CSV / Markdown 输出路径和结构未定义（v3 新增）

- 最终版 P0-4 第 202 行只说「导出 CSV / Markdown」，没说路径、文件名、内容结构。
- 待补充：
  - 路径建议：`data/exports/runs/{run_id}/{strategy}_{period}.csv`。
  - Markdown 报告结构：run_id、汇总计数（hit/watch/rejected/data_missing/error）、命中清单、剔除原因分布、data_missing 清单。
  - 复盘入口：建议加一个 `scripts/review_run.py` 按 run_id 拉起 Markdown 报告。

### 3.9 `disclosures` 扩列的 ALTER TABLE 语义未说（v3 新增）

- 最终版第 383-391 行给 `disclosures` 增加 `report_type` / `pdf_path` / `ingested_at` / `status` / `error` 列，但没说是 ALTER 还是重建。
- DuckDB 支持 `ALTER TABLE ADD COLUMN`，但需注意：
  - 已有行的新增列默认 `NULL`，需要决定是否回填。
  - 如果用 `CREATE TABLE IF NOT EXISTS` 不会扩列，老库会缺字段。
- 待补充：
  - 明确使用 `ALTER TABLE disclosures ADD COLUMN IF NOT EXISTS ...`（DuckDB 1.3+ 支持）。
  - 老库迁移函数：检查列存在性，缺失则补。
  - 历史行的 `report_type` 可以从 `period` 推导（如 `2024A` → `annual`），其他字段留 NULL。

## 四、执行计划复核（v4 新增）

针对改进文档「最终版 v2」新增的「执行计划」（步骤 0-7）的复核。

整体结构合理——每步独立验收、P0→P1→P2 顺序正确、"暂不做"清单清晰、与 P0-1~P2-3 对应关系明确。**但有 5 个会踩坑的具体问题**：

### 4.1 缺 baseline 快照，「不大幅漂移」无法判断

- 步骤 2 验收要求「现有 `target_pool.csv` 和 `target_pool_overseas.csv` 的命中结果不出现无解释的大幅漂移」，但**步骤 0/1 都没存 baseline**。
- 没有基准快照，"漂移"只能凭感觉判断。
- 建议：步骤 0 末尾或步骤 1 末尾加一条——把当前 `target_pool*.csv` 复制到 `data/baselines/pre_stateful_{date}/`，步骤 2 验收时 diff 这个目录。

### 4.2 步骤 3 默认 `max_workers=4` 风险高

- 改进文档 P0-9 第 389 行和步骤 3 第 849 行都写 `max_workers=4`，但**东财接口必须串行**（`src/collectors/_em_throttle.py` 强制 `EM_MIN_INTERVAL=1.0s`）。
- 4 个线程并发打东财会触发风控，30 只样本跑一半就废。
- 建议：
  - 默认 `max_workers=1`（全串行），把 `ThreadPoolExecutor` 框架留出来。
  - 只在 PDF 下载、新浪请求这两个非东财环节启用并发，上限 2-3。
  - 步骤 4 跑通后再调优，不要在步骤 3 默认就开 4。

### 4.3 步骤 2 工作量被低估（策略代码状态化是大改）

- 步骤 2 把「策略一/三从只给命中改成全量状态化」作为单个步骤，但这是**最大的代码改造**：
  - 当前 `src/strategies/consumer_reversal.py` / `overseas_champion.py` 大概率是直接 `to_csv()` 输出命中清单的形态。
  - 要改成每只候选股都有 `status / hit_reason / reject_reason / metrics_json`，整个数据流要重构。
  - 还要新增 watch 状态映射（`parse_warning / consensus_missing / near_threshold`），这些在现有代码里完全不存在。
- 建议步骤 2 拆为：
  - **2a**：定义 `ScreeningResult` dataclass + `src/screening/status.py` 枚举常量 + 原因码映射。
  - **2b**：策略一改造 + 测试。
  - **2c**：策略三改造 + 测试。
  - **2d**：跑 baseline diff，确认命中清单无突变。

### 4.4 错误恢复没说（`screen_runs` 脏数据）

- 步骤 3 流程写 `创建 screen_runs(status=running)` → 跑 → `更新 screen_runs(status=success/partial/failed)`。
- **但如果跑到一半进程被 Ctrl-C 或 OOM 杀掉**，会留下 `status=running` 的脏数据，后续查询会困惑。
- 建议补充：
  - 启动时扫 `WHERE status='running' AND started_at < now()-1h`，更新为 `status=failed, error='process_killed'`。
  - 或者用 PID + 心跳机制（每 30s 更新 `last_heartbeat`），启动时清理超时心跳。

### 4.5 步骤 5 过大，步骤 7 把无关任务绑一起

- 步骤 5 把 P1-1 ~ P1-5 全塞一起，**工作量是步骤 0-4 之和的 2-3 倍**。建议拆：
  - **5a**：P1-4 + P1-5 数据源分层 + baseline（必须先做，否则后续无法判断字段漂移）。
  - **5b**：P1-3 海外收入解析增强（独立，可单独验收）。
  - **5c**：P1-1 + P1-2 策略一/三新信号（依赖 5a 的数据源稳定）。
- 步骤 7 把 P2-2（季报）和 P2-3（RAG）放一起，**两者无强依赖**。建议拆 7a/7b，谁先做完谁先合。

### 4.6 小问题（不阻塞但建议改）

- **缺时间估算**：8 个步骤没给预期工作量，无法排期。建议每步标注「≈N 天」。
- **步骤 0 措辞不准**：写「补策略三海外收入异常过滤测试」，但 `overseas_ratio_abnormal` 这个原因码是步骤 2 才定义的，步骤 0 只能测现有硬过滤，应改措辞为「补策略三现有海外收入硬过滤测试」。
- **步骤 4 没说人工抽查工作量**：30 只 × 5 类样本的人工 review 约需 0.5-1 天，应在交付物里注明。

### 4.7 建议补充的 2 件事

1. **加「环境前置」小节**（步骤 0 之前）：明确 DuckDB 文件路径、`tests/` 用 pytest、Python 版本、依赖锁文件（`requirements.txt` 或 `uv.lock`），避免开工后发现环境问题。
2. **加「rollback 策略」小节**：每个步骤如果跑挂了怎么回滚——比如步骤 1 改了 schema 后跑步骤 2 出问题，能否 disable 新表回到老流程。

### 4.8 执行计划部分总体观感

执行计划方向正确，**主要问题是步骤 2 和步骤 5 工作量估算偏小**，加上 `max_workers=4` 这个并发默认值会直接踩东财限流的坑。其他都是细节。如果按 4.1-4.5 修订，整个 P0 闭环（步骤 0-4）约需 2-3 周，P1（步骤 5）拆分后约需 3-4 周。

## 落地优先级建议

最终版 P0/P1/P2 划分基本合理。补充建议：

- **P0 增补**（否则两周内跑不通）：
  - `metrics_json` schema 规范（3.1）—— 表建好就要定。
  - `disclosures` 覆盖率核验和 fallback（3.2）—— `--from-disclosures` 的前提。
  - 失败语义和 `--resume`（3.3）—— 流程跑通就要面对失败。
  - 表创建位置和迁移（3.4）—— 一次性写对，避免后续 ALTER。
  - `watch` 状态语义（3.6）—— 和 P0-3 剔除原因一并定义。
- **P1 增补**：
  - 数据源切换 baseline 回归测试（3.5）—— P1-4 数据源分层的前置条件。
- **P2 增补**：
  - `config_json` 完整结构（3.7）—— P2-1 评分层落地时一并定。
- **不阻塞但建议早定**：
  - CSV / Markdown 输出结构（3.8）—— 影响复盘体验。
  - `disclosures` 扩列 ALTER 语义（3.9）—— 一次性写对。
  - 并发模型 / 超时 / 断点续跑（2.1）—— 100 家以上跑前必须定。

## 总体观感

最终版方向正确，优先级清晰，对 v1/v2 review 的吸收也基本完整。v3 主要补的是 **P0 落地的执行细节**：schema、失败语义、覆盖率、迁移、状态语义。这些不影响方向，但会直接影响 P0 能不能在两周内真正跑通——`metrics_json` 半年后乱七八糟、`disclosures` 只有 30 家、跑 100 只用了 6 小时、`watch` 和 `rejected` 混淆导致复盘看不懂，都是这些细节没定会踩的坑。
