# find-share

A 股选股框架：基于公开数据的量化初筛 + 研报深度分析的两阶段工作流。

## 策略状态

| 策略 | 逻辑 | 当前命中 |
|------|------|---------|
| **消费股反转** | 食品饮料/家电/美容护理/商贸零售/纺服/社会服务/轻工 + PE 分位<30%（**3y/5y/10y 可选**）+ 扣非同比>30% + **反转判定** + 经营质量过滤（现金流/扣非质量）| **4 只** |
| **医药量价齐升** | 重构为二A“集采修复型”和二B“创新药/创新器械出海型”；二A MVP 已支持集采事件 + 财务修复 + PB 分位软约束，港股/A+H 映射可入库 | P0 数据闭环完成，待实盘筛选校准 |
| **出海隐形冠军** | 机械/汽车/化工 + 海外收入占比 30%~95% + PE<25 + **现金流/净利≥0.7** + **资产负债率<60%** + 海外同比>40%（连续两年数据可得时强制；单年降置信度）+ **被忽视证据链**（研报/新闻/概念聚合）| **15 只** + 50 只扩展池候选 |

## 功能模块

### 数据采集层 `src/collectors/`

| 模块 | 功能 | 文件 |
|------|------|------|
| `AStockSkillSource` | `$a-stock-data` 口径 A 股直连源：腾讯估值快照 + 新浪三表汇总，不再默认 fallback AkShare | `a_stock_skill_source.py` |
| `AkShareSource` | 历史兼容实现；默认财报季筛选已改用 `AStockSkillSource` | `akshare_impl.py` |
| `LocalCachedSource` | **P1.5-1**：装饰 DataSource，先读 DuckDB `pe_pb_history`/`financials`，缺失才 fallback 到当前 upstream + 回写 | `cached_impl.py` |
| `NeglectEvidenceCollector` | **P1.5-3**：东财个股新闻 + AI/半导体概念识别 + 同花顺热点频次 + 近 60 日相对收益 + 被忽视证据聚合 | `neglect_evidence.py` |
| `SinaFinancialSource` | 新浪财报三表（利润表/资产负债表/现金流量表），70+ 科目中英映射 | `sina_impl.py` |
| `EastMoneyResearchSource` | 东财研报列表（评级 + 三年 EPS 预测）+ PDF 下载（pdf.dfcfw.com 带 Referer 鉴权）| `eastmoney_research.py` |
| `ThsForecastSource` | 同花顺一致预期 EPS（独立口径，用于交叉验证东财）| `ths_forecast.py` |
| `global_stock_mapping` | 港股/A+H 代码格式归一，输出 Yahoo/东财 `SECUCODE`/`secid`，支持 CSV 入库 | `global_stock_mapping.py` |
| `em_get` | 东财统一限流（≥1s 间隔 + 随机抖动 + Session 复用 + 跳过 Clash 代理）| `_em_throttle.py` |
| `CnInfoDownloader` | 从巨潮下载定期报告 PDF（年报/半年报/一季报/三季报四类通用）| `cninfo_downloader.py` |
| `EmWebClient` | 东财 F10 拿全市场行业映射（5207/5527 = 94.2%）| `emweb_client.py` |
| `annual_report_parser` | 年报 PDF 提取境外收入（单位识别 + 跨页去重 + 合理性校验 + **P1.5-2 多 high 候选取小值** + 外销/其他国家和地区口径）| `annual_report_parser.py` |
| `DataSource` Protocol | 抽象层，未来切 Tushare 时业务代码零修改 | `base.py` |

### 存储层 `src/storage/`
DuckDB 17 张表：
- 静态数据：`stocks` / `industry_first` / `industry_second` / `stock_industry`
- 估值/财务：`pe_pb_history` / `financials` / `financials_full`（新浪三表长格式）
- 研报：`broker_reports`（东财研报列表）/ `eps_forecast_consensus`（同花顺一致预期）
- 医药：`pharma_vbp_events`（集采/中标结构化事件，要求 `source_url` + `evidence_text` 可追溯）
- 港股/A+H：`global_stock_mappings`（`hk_code`、Yahoo symbol、东财 `SECUCODE/secid`、`hk_disclosure_source_gap`）
- 其他：`overseas_revenue`（年报附注境外收入）/ `disclosures`（披露日历）
- **审计（P0）**：`screen_runs`（运行级）/ `candidate_scores`（个股级）
- **验证（P2）**：`backtest_results`（run 后 20/60/120 交易日前瞻收益）
- **兑现（P2）**：`financial_validation_results`（run 后下一期财务兑现验证）

全部带 upsert，重复入库保留 `pdf_path` / `ingested_to_rag` / 人工标注状态。

### 指标层 `src/indicators/`
- `valuation.py`：PE/PB 历史分位（**P1.5-4：参数化 3y/5y/10y 窗口**）
- `growth.py`：扣非净利润 TTM 计算 + 同比增速

### 策略层 `src/strategies/`
- `consumer_reversal.py`：策略一 + 反转判定 + P1.5-4 行业覆盖扩展（含化妆品/医美/纺服/服务消费/轻工）+ PE/PB 分位窗口参数化 + 经营质量过滤（每股经营现金流、扣非/归母净利背离）
- `pharma_strategy.py`：策略二行业池 + 策略二A集采修复型 MVP，区分集采修复型与创新药/创新器械出海型；二A 高 PB 分位只降级 watch，不硬剔除
- `overseas_champion.py`：策略三 + 3 个扩展过滤 + P1.5-2 多 high 候选 ratio 校验（candidates_json 兜底）+ 海外同比可用即校验 + P1.5-3 被忽视证据链
- `filters.py`：质量/流动性过滤（剔除 ST/金融/地产/低市值/极端 PE）

### 研报知识层 `src/knowledge/`
jieba 分词 + TF-IDF + SQLite 实现的轻量 RAG（不依赖 chromadb，秒级启动）。
- `index_directory(pdf_dir)` 批量索引
- `ingest_pdf(pdf_path, metadata)` 单文件入库（含显式 metadata 覆盖文件名解析）
- `query(question, top_k)` TF-IDF 余弦相似度检索
- **P1.5-5 同义词扩展**：查询自动扩展同义词组（海外/境外/国外/出口/国际，订单/中标/签约 等 10 组），解决"海外 vs 境外"召回率问题
- **P2-7 RAG 去重与章节标题**：按内容 hash 去重，避免同一研报不同文件名重复入库；chunk 记录 `section_title`，检索结果显示章节上下文

### Pipeline 编排 `src/pipeline/` + `scripts/`
**P2-0 统一 CLI**：`python3 -m src.pipeline.cli <subcommand>`，封装核心流水线子命令。
覆盖 bootstrap → 缓存预热 → 策略筛选 → 研报拉取 → RAG 检索 → 动态监控 → P0 审计/标注 → 策略二A医药筛选 → 港股/A+H 映射 全流程。

### 评分与监控层 `src/screening/`（P2 新增）
- **P2-1 评分层**（`scoring.py`）：线性模型 `growth*0.30 + valuation*0.20 + quality*0.20 + catalyst*0.15 + neglect*0.15 - risk_penalty`；缺失子分按 0.5 中性填充并输出 `coverage_ratio`；0 分位按真实低位处理；只用于排序和 watch 分层，不覆盖硬风控
- **P2-2 报告期参数化**（`period.py`）：`parse_period("2025A"/"2025H"/"2025Q1"/"2025Q3")` 返回 `PeriodInfo`；季报场景跳过海外收入硬过滤
- **P2-3 动态监控**（`run_diff.py` + `scripts/monitor_changes.py`）：对比两次 `screen_run`，输出 new_hit / dropped_hit / status_changed / metric_changed / new_parse_warning 五类事件
- **P2-4 一致性校验**（`consistency.py`）：研报 EPS 预测 vs 财报实际 EPS（偏差>25% → warn）+ 财报海外收入 vs 研报标题关键词匹配；批量预加载避免 N+1
- **P0/P2 审计修复**：`p0-audit` 采用严格口径，研报必须有本地 PDF，ground truth 必须通过合法标签/原因校验，VBP 事件必须带可追溯证据，且要求 `docs/pharma_ground_truth_rulebook.md` 和港股/A+H 映射存在；`--strategy all` 的 `config_json` 保存所有子策略配置
- **P2-6 前瞻收益回测**（`backtest.py` + `scripts/backtest_forward_returns.py`）：对 `hit/watch` 候选计算 20/60/120 交易日绝对收益和可选基准相对收益，结果入 `backtest_results`
- **P2-8 下一期财务验证**（`financial_validation.py` + `scripts/validate_next_financials.py`）：对 `hit/watch` 候选推导下一报告期，验证收入/净利同比是否继续兑现，结果入 `financial_validation_results`

## 当前交接快照（2026-07-05）

最近一轮财报季主 run：

- `run_id=20260704_112743_614f_2025A`
- 输出目录：`data/exports/runs/20260704_112743_614f_2025A/`
- 状态：`partial_success`
- 结果：策略三 `hit=1`（`001325 元创股份`）、`watch=6`（`000837`/`001207`/`001231`/`001239`/`001288`/`002145`）；策略一仍主要卡在 `pe_history_missing`
- 交接文件：`data/exports/latest_candidate_evidence.md`、`data/exports/human_label_queue.csv`、`data/exports/p0_audit.md`
- P0 审计：`python3 -m src.pipeline.cli p0-audit --period 2025A --strategy all` 已返回全 OK。

已完成的数据补齐：

- `$a-stock-data` 补数入口已覆盖最近缺失池：32 只股票财务/估值补齐成功，默认补 2024/2025 年报与 2026Q1，不补 2023。
- 研报 P0 数量门槛已过：`broker_reports=263`，本地 PDF 元数据 `205`，`research_reports/` 下 PDF 文件 `219`，RAG chunks `2502`。
- 最新 7 只 `hit/watch` 已回写 `human_label` / `label_reason`，`candidate_labels=7`，`hit_watch_unlabeled=0`。
- 策略二 P0 样本已补齐：`pharma_vbp_events=8` 且均有 `source_url`/`evidence_text`，`pharma_vbp_ground_truth.csv` 30 条并通过校验。
- 境外收入表当前 `overseas_revenue=155`（2024 年 81 条，2025 年 74 条）；`001311`/`002085` 已通过 PDF parser 解析入库，置信度 high、无 parse_warning。
- A/H 映射已有第一批 5 条：药明康德、康龙化成、泰格医药、君实生物、百济神州，可供 `$global-stock-data` 港股扩展池继续接入。

当前剩余主线：

- P1：策略一真实历史 PE/PB 数据源仍是最大瓶颈，最新 run 主要卡在 `pe_history_missing`。
- P1：策略二A 需要基于已补的 30 条 ground truth 跑筛选、复盘标签质量，并继续扩充真实集采事件样本。
- P1：策略三仍需继续降低 parser warning 和失败样本；`001288` 仍依赖 candidates_json 兜底，2024 全量导入仍有 11 份 PDF 找到附注但未提取到境外行。
- P1：港股扩展池目前只有 A/H 映射层，尚未消费 `$global-stock-data` 的港股行情、财务、新闻、资金流。

推荐接手顺序：

```bash
cat data/exports/p0_audit.md
cat data/exports/latest_candidate_evidence.md

# P0 已全 OK；下一步建议从 P1/P2 校准开始
python3 -m src.pipeline.cli pharma-screen --period 2025A --limit 100
python3 -m src.pipeline.cli strategy1
python3 -m src.pipeline.cli screen --period 2025A --strategy all --limit 30
python3 -m src.pipeline.cli backtest --period 2025A --strategy all
```

---

## 快速开始

### 环境要求

- Python 3.9+
- 网络可访问东财/新浪/同花顺/cninfo 域名（建议关 Clash Verge 或加白名单）

### 安装

```bash
# 克隆
git clone git@github.com:jeffzou785/find-share.git
cd find-share

# 装依赖（pip 配置见下文「踩坑指南」）
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
# 默认配置即可运行，无需修改
```

### 统一 CLI（推荐）

```bash
# 查看所有子命令
python3 -m src.pipeline.cli list

# 初始化基础数据（首次运行，约 5 分钟）
python3 -m src.pipeline.cli bootstrap
python3 -m src.pipeline.cli bootstrap-industry                    # 全市场行业映射

# 预热本地财务/估值快照（P1.5-1，让策略跑得更快更稳）
python3 -m src.pipeline.cli refresh --limit 30

# 使用 $a-stock-data 口径补数：腾讯/新浪/巨潮/东财研报；默认不补 2023，包含 2026Q1
python3 -m src.pipeline.cli refresh-skill --from-run <run_id> --download-pdfs --parse-overseas
python3 -m src.pipeline.cli refresh-skill --codes 000420 000545 --annual-years 2024 2025 --q1-year 2026

# 跑策略（统一入口）
python3 -m src.pipeline.cli strategy1                              # 策略一：消费反转
python3 -m src.pipeline.cli strategy3                              # 策略三：出海隐形冠军

# 财报季主入口（状态化，落 screen_runs/candidate_scores 审计表）
python3 -m src.pipeline.cli screen --period 2025A --strategy all --limit 30
python3 -m src.pipeline.cli screen --period 2025A --strategy overseas --enable-neglect-evidence  # 开被忽视证据链
python3 -m src.pipeline.cli screen --period 2025Q1 --strategy overseas                          # 季报 + 默认评分

# 动态监控：对比最近两次 run
python3 -m src.pipeline.cli monitor --strategy overseas --period 2025A
python3 -m src.pipeline.cli monitor --before-run <run_id_old> --after-run <run_id_new> --output data/exports/run_diff.md

# P2 验证：前瞻收益回测（只使用本地 pe_pb_history，不联网）
python3 -m src.pipeline.cli backtest --period 2025A --strategy all
python3 -m src.pipeline.cli backtest --run-id <run_id> --benchmark-code 000300

# P2 兑现：下一期财务验证（只使用本地 financials，不联网）
python3 -m src.pipeline.cli financial-validate --period 2025A --strategy all
python3 -m src.pipeline.cli financial-validate --run-id <run_id> --validation-period 2026Q1

# P0 闭环：审计、人工标签、策略二医药结构化数据
python3 -m src.pipeline.cli p0-audit
python3 -m src.pipeline.cli p0-audit --period 2025A --strategy overseas
python3 -m src.pipeline.cli p0-audit --all-runs
python3 -m src.pipeline.cli label-export --output data/exports/human_label_queue.csv
# 填写 human_label / label_reason 后再导入
python3 -m src.pipeline.cli label-import data/exports/human_label_queue.csv
python3 -m src.pipeline.cli pharma-template
python3 -m src.pipeline.cli pharma-vbp --csv data/exports/pharma_vbp_events.csv
python3 -m src.pipeline.cli pharma-gt --csv data/exports/pharma_vbp_ground_truth.csv
python3 -m src.pipeline.cli pharma-screen --period 2025A --limit 100
python3 -m src.pipeline.cli global-map --init-template
python3 -m src.pipeline.cli global-map --csv data/exports/global_stock_mappings.csv

# 研报 + RAG
python3 -m src.pipeline.cli reports 600519 --max-pdfs 3
python3 -m src.pipeline.cli rag search "海外订单" --stock 600519
```

### 三条主命令（旧脚本）

```bash
# 1. 初始化基础数据（首次运行，约 5 分钟）
python3 src/pipeline/bootstrap.py                              # 全市场股票列表
python3 scripts/bootstrap_emweb_industry.py                    # 全市场行业映射（94% 覆盖率）

# 2. 跑策略一（消费反转 + 反转判定）
python3 scripts/run_phase2_strategy1.py
# → 输出 data/exports/target_pool.csv（4 只命中）

# 3. 跑策略三（出海隐形冠军 + 现金流/负债率过滤）
python3 scripts/run_phase3_strategy3.py
# → 输出 data/exports/target_pool_overseas.csv（15 只命中 + 50 只扩展池）
```

---

## 完整工作流

### 日常使用（数据已就绪）

```bash
# 跑策略
python3 scripts/run_phase2_strategy1.py
python3 scripts/run_phase3_strategy3.py

# 财报季主入口（推荐，状态化落审计表）
python3 scripts/run_after_disclosure.py --period 2025A --strategy all --limit 30

# 研报检索（需先跑过 import_research_reports.py 入库）
python3 scripts/research_rag_cli.py search "海外业务增速 出口"
python3 scripts/research_rag_cli.py search --stock 600519 "直销 i茅台"
python3 scripts/research_rag_cli.py info                # 查看 RAG 状态
```

### 预热本地缓存（P1.5-1，新增）

```bash
# 全候选池预热 PE/PB 当前快照 + 财务摘要到本地 DuckDB
python3 scripts/refresh_financials_and_valuation.py

# 只跑指定股票 / 限制数量 / 按行业过滤
python3 scripts/refresh_financials_and_valuation.py --codes 600031 601058
python3 scripts/refresh_financials_and_valuation.py --limit 30
python3 scripts/refresh_financials_and_valuation.py --industries 机械设备 食品饮料

# 强制覆盖本地已有数据
python3 scripts/refresh_financials_and_valuation.py --force
```

预热后，`run_after_disclosure.py` / `run_phase2_strategy1.py` / `run_phase3_strategy3.py`
通过 `LocalCachedSource` 自动从本地读；缺失时默认 fallback 到 `$a-stock-data`
口径的 `AStockSkillSource`（腾讯估值快照 + 新浪三表）并回写。注意：
腾讯只提供当前 PE/PB 快照，不伪造 3/5/10 年历史分位。

### 拉研报（新增）

```bash
# 一站式：东财研报列表 + 同花顺一致预期 + PDF 下载 + RAG ingest
python3 scripts/import_research_reports.py 600519 --max-pdfs 3
python3 scripts/import_research_reports.py 600519 000858 --max-pages 2 --max-pdfs 5

# 分步控制
python3 scripts/import_research_reports.py 600519 --skip-pdf      # 只拉列表 + 一致预期
python3 scripts/import_research_reports.py 600519 --skip-ths      # 跳过同花顺
python3 scripts/import_research_reports.py 600519 --skip-rag      # 只下 PDF 不入 RAG
```

### 拉财报 PDF（年报 / 半年报 / 季报）

```bash
python3 scripts/download_annual_reports.py 600519                              # 年报（默认）
python3 scripts/download_annual_reports.py 600519 --report-type half_year --year 2024
python3 scripts/download_annual_reports.py 600519 --report-type q1 --year 2025
python3 scripts/download_annual_reports.py --extension --limit 50              # 扩展池批量
```

### 数据维护（按需）

```bash
# 刷新行业映射（财报季、IPO 多时跑）
python3 scripts/bootstrap_emweb_industry.py

# 入库新下载的年报境外收入（P1.5-2：多 high 候选自动取合理值）
python3 scripts/import_overseas_revenue.py
python3 scripts/import_overseas_revenue.py 2025 --skip-f10-fallback  # 需要排查 PDF parser 时可关闭 F10 fallback
python3 scripts/import_overseas_revenue.py 2025 --codes 001311,002085 # 只重跑指定股票，便于修 parser 难例

# 重新跑策略三（新数据生效）
python3 scripts/run_phase3_strategy3.py

# 索引本地研报目录（research_reports/ 下手动放的 PDF）
python3 scripts/research_rag_cli.py index
```

### 查看结果

```bash
cat data/exports/target_pool.csv                       # 策略一 4 只
cat data/exports/target_pool_overseas.csv              # 策略三 15 只
cat data/exports/overseas_extension_candidates.csv     # 策略三扩展池 50 只
open data/exports/strategy1_result.md
open data/exports/strategy3_result.md

# 状态化 run 的输出（财报季主入口）
ls data/exports/runs/                                  # 每次 run 一个子目录
cat data/exports/runs/<run_id>/report.md               # Markdown 总报告
cat data/exports/runs/<run_id>/coverage.md             # 字段覆盖率 / 原因码 / 数据源状态
cat data/exports/backtests/<run_id>/summary.md         # 20/60/120 日前瞻收益回测
cat data/exports/financial_validations/<run_id>/summary.md  # 下一期财务兑现验证
```

### 找机械行业出海标的（完整示例）

```bash
# 1. 先看现有命中
cat data/exports/target_pool_overseas.csv
# → 已有 15 只（赛轮、三一、中联、杰克、福耀等）

# 2. 看扩展池候选（PE<25 但年报未入库的 50 只）
head -20 data/exports/overseas_extension_candidates.csv

# 3. 挑感兴趣的（如宇通客车、中国中车），下载年报
python3 scripts/download_annual_reports.py 600066 601766

# 4. 入库并重跑
python3 scripts/import_overseas_revenue.py
python3 scripts/run_phase3_strategy3.py

# 5. 拉这些标的的研报做深度分析
python3 scripts/import_research_reports.py 600066 601766 --max-pdfs 3
python3 scripts/research_rag_cli.py search "宇通客车 海外订单" --stock 600066
```

---

## 待升级项（按优先级）

### P0 - ✅ 闭环完成

| 阶段 | 完成内容 |
|------|---------|
| **海外收入数据覆盖**（2026-06-15）| 56 只候选下载 2024 + 2025 年报，入库 93 条境外收入记录；策略三从 6 → 24 只 |
| **数据合理性过滤**（2026-06-15）| `overseas_champion.py` 加 `overseas_ratio<0.95` + 同比异常剔除 |
| **数据源扩充 Phase 1**（2026-06-19）| 新浪财报三表（`sina_impl.py`）+ cninfo 季报/半年报支持 |
| **数据源扩充 Phase 2**（2026-06-19）| 东财研报 + 同花顺一致预期 + DuckDB 3 张新表（`financials_full`/`broker_reports`/`eps_forecast_consensus`）+ RAG `ingest_pdf` |
| **策略精度提升**（2026-06-19）| 策略一加反转判定（24→4 只真反转）；策略三加现金流/负债率过滤（24→15 只）+ 一致预期可选过滤 |
| **P0 状态化改造 + 事件驱动入口**（2026-06-27）| screening 抽象层（Status / ScreeningResult / MetricsSchema）+ screen_runs/candidate_scores 审计表 + `run_after_disclosure.py` + baseline_diff.py + 161 测试覆盖 |
| **状态化改造修复**（2026-06-27）| 修 `run_overseas_champion` dup `_evaluate_one` 导致旧 CSV 入口不走状态化路径（P1-2/P1-3 增强失效）；修 resume 多策略 fingerprint 不一致；修 AkShare `revenue_yoy`/`gross_margin` 百分数单位；`cleanup_stale_screen_runs` 改用 INTERVAL 字面量 |
| **P0 闭环工具**（2026-07-01）| 新增 `p0-audit`/`label-export`/`label-import`/`pharma-vbp`/`pharma-gt`；研报元数据覆盖已达 200/200，P0 审计改为要求本地研报 PDF 可用；当时暴露的人工标签、策略二 ground truth、医药集采事件缺口已在 2026-07-05 闭环补齐 |
| **P0 对抗式审计修复**（2026-07-02）| `p0-audit` 不再给假绿灯：本地研报 PDF 不足、坏 ground truth、空 VBP 证据都会返回 TODO；标注默认按最新成功/部分成功 run 审计，避免历史 run 污染 |
| **策略二A 工程闭环 MVP**（2026-07-03）| 新增 `pharma-template` 初始化模板、`pharma-screen` 集采修复型筛选入口、`docs/pharma_ground_truth_rulebook.md` 标注规则；`stock_industry` 保留 `sw_second/em2016` 等行业增强列，筛选结果落 `screen_runs/candidate_scores` |
| **策略二A PB软约束 + A/H映射闭环**（2026-07-03）| `pharma-screen` 读取本地 `pe_pb_history` 计算 PB 分位，超过软阈值只降级 watch；新增 `global-map`、`global_stock_mappings` 表和 P0 审计项，为策略二B港股扩展池/A+H 对照做前置 |
| **P0 数据补齐与交接快照**（2026-07-04）| `refresh-skill` 补齐最新缺失池 32 只；最新 `screen` run 为 `20260704_112743_614f_2025A`；研报 PDF/RAG 达 P0 门槛（263 条元数据、205 份本地 PDF、219 个 PDF 文件、2502 个 RAG chunks）；A/H 映射已有 5 条 |
| **策略三估值原因码拆分**（2026-07-04）| 负 PE/估值缺失不再归入 `financial_data_missing`；估值源缺失返回 `valuation_data_missing`，非正或空 PE-TTM 返回 `pe_ttm_invalid`，并写入 `source_status.extra.valuation_missing_reason` |
| **海外收入 parser 难例第一轮**（2026-07-04）| 修复“万吨/亿吨”等数量单位被误判成收入；当前年 0.00 的分地区行不再回退抓上一年金额；策略三低占比 best 候选会尝试 candidates_json 兜底；新增 `tests/fixtures/overseas_revenue_golden_cases.csv` 跟踪 `001288`/`002145`/`001311`/`002085` |
| **海外收入 F10 fallback 入口**（2026-07-04）| 新增 `f10_overseas_revenue.py`，PDF 解析失败时 `import_overseas_revenue.py` 默认尝试 mootdx F10 主营构成；无 mootdx/网络失败时保留原 PDF error，可用 `--skip-f10-fallback` 关闭；后续发现 `001311`/`002085` F10 文本为空，已改由 PDF parser 关键词补全解决 |
| **P0 闭环收尾**（2026-07-05）| 最新 7 只 hit/watch 已回写人工标签；策略二 VBP 事件 8 条、ground truth 30 条通过校验；`001311`/`002085` 通过外销/其他国家和地区口径解析入库；`p0-audit --period 2025A --strategy all` 全 OK |

### P1 - ✅ 已完成（2026-06-27）

| 功能 | 完成内容 |
|------|---------|
| **P1-3 海外收入解析增强**| 总营收/合计行标记 + 同页境内+境外判定置信度 + 跨页 `\n` 修复 + `select_best_record` 优先高置信度 + 多年交叉校验 + 候选保留到 `candidates_json` + `overseas_revenue` 表扩 3 列 |
| **P1-5a 数据源 baseline 工具**| 30-50 只样本股的 AkShare vs 新浪对照 + 差异分布表 + 不可替换字段清单 + 单源缺失统计 |
| **P1-1 策略一新信号**| PB 5 年分位 + 营收同比 + 毛利率同比改善验证 + 数据缺失降级 watch 不直接剔除 |
| **P1 策略一经营质量过滤**（2026-07-03）| 新增每股经营现金流正向验证与扣非/归母净利背离过滤；字段缺失进入 watch，明确不达标进入 rejected |
| **P1 策略三海外同比守卫**（2026-07-03）| 连续两年海外收入可用时默认强制校验同比>40%；单年数据不硬拒绝，写入 `source_status.extra.overseas_yoy_status=single_year` 降低置信度 |
| **P1-2 策略三"被忽视"信号**| 研报覆盖度 `reports_count_90d` 填入 metrics.catalyst；不改硬过滤 |

### P1.5 - ✅ 已完成（2026-06-28）

| 功能 | 完成内容 |
|------|---------|
| **P1.5-1 财务/估值本地落库**（`cached_impl.py` + `refresh_financials_and_valuation.py`）| `LocalCachedSource` 先读 DuckDB `pe_pb_history`/`financials`，缺失才 fallback + 回写；默认 fallback 为 `$a-stock-data` 当前估值快照 + 新浪三表；策略代码透明切换，历史 PE/PB 分位仍依赖本地已有样本 |
| **P1.5-1b `$a-stock-data` 补数入口**（2026-07-04）| 新增 `AStockSkillSource` 与 `refresh-skill`：按腾讯/新浪/巨潮/东财研报口径补 A 股数据；默认补 2024/2025 年报和 2026Q1，不处理 2023 |
| **P1.5-2 海外收入困难样本**（`annual_report_parser.py` + `overseas_champion.py`）| `select_best_record` 多 high 候选 max/min > 5x 时取**最小**（避免误抓总营收，600690 案例）+ 策略层 `_pick_plausible_candidate` 从 `candidates_json` 选合理 ratio + 关键词扩展（美洲/欧洲/亚洲等区域名词） |
| **P1.5-3 被忽视证据链**（`neglect_evidence.py`）| `NeglectEvidenceCollector`：东财个股新闻数（`news_count_30d`）+ AI/半导体概念识别（`is_ai_related`）+ 同花顺热点频次（`hot_reason_count_30d`）+ 近 60 日相对基准收益（`relative_return_60d`）+ `compute_neglect_evidence` 聚合可读证据；不改硬过滤，仅填 metrics.catalyst |
| **P1.5-4 策略一行业覆盖+窗口参数化**（`consumer_reversal.py`）| `TARGET_INDUSTRIES` 扩展（化妆品/个护/医美/纺服/服务消费/轻工）+ `SUPPORTED_HISTORY_WINDOWS = (3, 5, 10)` + PE/PB 分位三窗口全填入 metrics + `history_years` 校验 |
| **P1.5-5 RAG 语义质量升级**（`research_rag.py`）| 同义词词典（10 组：海外/境外/出口、订单/中标/签约、欧洲、东南亚 等）+ `expand_query_synonyms` 自动扩展查询词 + 单元测试覆盖 |
| **P1.5-6 legacy CSV 字段补全**（`overseas_champion.py`）| `_result_to_legacy_dict` 补齐 `ocf_net_yi`/`net_profit_yi`/`total_liabilities_yi`/`total_assets_yi`/`eps_current`/`eps_forecast_y1/y2`/`eps_y1/y2_growth` 9 个字段，状态化后信息量不降级 |

### P2 - ✅ 已完成（2026-06-28）

| 功能 | 完成内容 |
|------|---------|
| **P2-0 统一 CLI** ✅（2026-06-28）| `python3 -m src.pipeline.cli <subcommand>`，封装核心流水线子命令（bootstrap/refresh/screen/strategy1/strategy3/reports/pdf/rag/baseline/monitor/P0 审计与标注等）|
| **P2-1 评分层**（`src/screening/scoring.py`）| 按策略差异化默认权重；子分缺失按 0.5 中性填充并输出 `coverage_ratio`；风险扣分累加 parse warning/现金流/负债风险；财报季 screen 默认写入 `metrics.score.final_score` |
| **P2-2 半年报/季报扩展**（`src/screening/period.py`）| `parse_period("2025A/H/Q1/Q3")` → `PeriodInfo(kind, year, has_overseas_notes)`；季报场景跳过 `overseas_ratio` 硬过滤，PE/cashflow/leverage 仍生效；半年报与年报保持完整过滤；7 个新测试覆盖 |
| **P2-3 动态监控**（`src/screening/run_diff.py` + `scripts/monitor_changes.py`）| `diff_runs(before, after)` 输出 5 类事件：new_hit / dropped_hit / status_changed / metric_changed / new_parse_warning；阈值化指标 diff（PE 变化>5、研报数>3 等）；CLI `monitor` 子命令；17 个测试覆盖 |
| **P2-4 财报 vs 研报一致性校验**（`src/screening/consistency.py`）| 研报 EPS Y1 预测 vs 财报实际 EPS（偏差>25% → warn）+ 财报海外收入 vs 研报标题关键词匹配（被忽视信号 → warn）；输出软证据，不改硬过滤；批量预加载避免 N+1；11 个测试覆盖 |
| **P2-5 Tushare 兜底**（`src/collectors/tushare_impl.py`）| `TushareSource` stub 锁定 DataSource Protocol 形状；无 token 时实例化允许、方法调用抛 NotImplementedError 提示激活路径；未来切 Tushare 业务代码零修改；3 个测试覆盖 |
| **P2 对抗式 review 修复**（2026-07-02）| 修 `0.0` PE/PB 分位被 `or` 误判缺失；`--strategy all` 保存多子策略配置；coverage 报告写入 `screen_runs.coverage_json` 和 run 输出目录 |
| **P2-6 前瞻收益回测**（2026-07-02）| 新增 `backtest` CLI：按 screen_run 的 `hit/watch` 候选计算 20/60/120 交易日收益，支持可选基准相对收益，落 `backtest_results` 和 `data/exports/backtests/<run_id>/` |
| **P2-7 RAG 去重与章节标题**（2026-07-02）| `ResearchRAG` 新增 `content_hash` / `text_hash` / `section_title`，同内容不同文件名不重复入库；搜索结果输出章节标题，为后续 claim 抽取保留上下文 |
| **P2-8 下一期财务验证**（2026-07-02）| 新增 `financial-validate` CLI：按 screen_run 的 `hit/watch` 候选推导下一报告期，验证收入/净利同比是否兑现，落 `financial_validation_results` 和 `data/exports/financial_validations/<run_id>/` |

### 已知问题

- 海外收入多 high 候选时，P1.5-2 已自动取最小值并标 parse_warning；策略层 candidates_json 兜底已落地，兜底后海外同比会按修正值重算，但极端 case 仍可能漏过（需配合人工 review）
- 海外收入解析仍有 11 份 2024 PDF 找到附注但未提取到境外行（pdfplumber 表格结构识别限制；不阻塞当前 P0，但属于后续 parser review 池）
- P0 审计采用严格口径：研报必须有本地 PDF，ground truth 必须通过合法标签/原因校验，VBP 事件必须带 source_url/evidence_text，标注规则文档和 A/H 映射也必须存在；当前 `p0-audit --period 2025A --strategy all` 已全 OK
- 核心 P0 pipeline 已接入 logging；部分 legacy 辅助脚本仍保留 print
- 东财研报 EPS Y1/Y2 "今年/明年"口径跨年（部分发布日期早的研报"今年"指上一年），与同花顺固定年度口径有偏差，交叉验证时注意

---

## 踩坑指南（重要）

### 1. Clash Verge 拦截 pip

**症状**：`pip install` 卡住或 SOCKS 错误。

**解决**：把清华源写进 `~/.pip/pip.conf`：

```ini
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
proxy =
trusted-host = pypi.tuna.tsinghua.edu.cn
```

### 2. 东财 push2.eastmoney.com 反爬

**症状**：`stock_individual_info_em` / `stock_board_industry_cons_em` 报 `RemoteDisconnected` 或 `TLS error`。

**原因**：东财对 Python TLS 指纹反爬。

**解决**：本框架已统一用 `emweb.eastmoney.com`（F10）和 `reportapi.eastmoney.com`（研报），所有东财接口走 `em_get()` 且 `trust_env=False` 跳过 Clash 代理。

### 3. setuptools 太新导致 jieba 报错

**症状**：`AttributeError: module 'pkg_resources' has no attribute 'resource_stream'`

**解决**：降级 setuptools
```bash
pip install "setuptools<70"
```

### 4. 历史 AkShare 兼容路径

**症状**：旧脚本或历史 baseline 工具显式调用 `AkShareSource` 时，可能遇到 `stock_value_em` 返回空，或本地 Python 环境未安装 `akshare`。

**解决**：财报季主筛选入口已默认改用 `AStockSkillSource`（腾讯估值快照 + 新浪三表）并由 `LocalCachedSource` 回写 DuckDB，缺少 AkShare 不影响 `screen` / `strategy1` / `strategy3` 默认运行。只有需要跑历史 `data_source_baseline.py` 或 `akshare_impl.py` 兼容路径时，才需要补装并排查 AkShare。

### 5. 申万行业成分股接口有 bug

**症状**：`sw_index_third_cons` 报 `Length mismatch`。

**解决**：本框架改用东财 F10 + 新浪行业双源融合。

### 6. 新浪财报科目漂移

**症状**：`SinaFinancialSource` 打印 `[sina] WARN ... 有 N 个科目未映射`。

**原因**：新浪 API 偶尔增减科目，或新会计准则科目（使用权资产/租赁负债等）未在映射表。

**解决**：不影响主流程——未映射科目以 `item_cn` 原文落库（`item_en` 留空），需要时扩 `sina_impl.py:ITEM_CN_MAP`。

---

## 项目结构

```
find-share/
├── src/
│   ├── collectors/                  # 数据采集层
│   │   ├── base.py                  # DataSource Protocol
│   │   ├── a_stock_skill_source.py  # $a-stock-data 口径 A 股直连源
│   │   ├── akshare_impl.py          # AkShare 历史兼容实现
│   │   ├── cached_impl.py           # P1.5-1 LocalCachedSource
│   │   ├── neglect_evidence.py      # P1.5-3 被忽视证据 collector
│   │   ├── sina_impl.py             # 新浪财报三表
│   │   ├── eastmoney_research.py    # 东财研报 + PDF
│   │   ├── ths_forecast.py          # 同花顺一致预期
│   │   ├── tushare_impl.py          # P2-5 Tushare 兜底 stub
│   │   ├── _em_throttle.py          # 东财统一限流
│   │   ├── emweb_client.py          # 东财 F10 行业映射
│   │   ├── cninfo_downloader.py     # 巨潮定期报告 PDF
│   │   ├── annual_report_parser.py  # 年报境外收入解析
│   │   ├── industry_mapping.py      # 申万↔新浪行业映射
│   │   └── _retry.py                # @akshare_call 装饰器
│   ├── storage/                     # DuckDB 持久化（17 张表）
│   ├── indicators/                  # PE/PB 分位（3y/5y/10y）+ TTM 增速
│   ├── strategies/                  # 策略一/三 + 过滤器 + 反转判定
│   ├── knowledge/                   # 研报 RAG（jieba+TF-IDF+SQLite+同义词扩展）
│   ├── screening/                   # 状态化抽象层 + 评分 + 期间解析 + run_diff + 一致性
│   └── pipeline/                    # 编排 + 统一 CLI
├── scripts/                         # 可独立运行的流水线脚本
├── data/
│   ├── duckdb/                      # 主数据库（不入库）
│   ├── pdfs/                        # 原始年报 PDF（不入库）
│   ├── cache/                       # AStock/Sina/RAG/AkShare兼容缓存（不入库）
│   └── exports/                     # CSV/MD 结果输出
└── research_reports/                # 研报文件夹（含 broker/ 子目录，不入库）
```

---

## 升级路径

| 触发条件 | 升级动作 | 工作量 |
|---------|---------|--------|
| `$a-stock-data` 直连源仍无法覆盖关键字段 | 评估 Tushare 2000 积分兜底（一次性约 2000 元）| `tushare_impl.py` stub 已有，接真实字段约 2-3 天 |
| 需要盘中实时监控 | 加 websocket 行情接入 | 接东财/雪球推送，1-2 天 |
| 研报量 > 100 篇 | 升级 RAG 到 embedding | metadata + 同义词 + 模板已落地，外部 embedding API 是下一步，本地模型最后评估 |
| 需要全市场一致预期过滤 | 批量跑 `import_research_reports.py` 后开 `require_consensus_growth=True` | 数据拉取 ~30 分钟 |
| 转专业投资者 | 上 Wind/iFinD | API 接入，1-2 周 |

---

## License

MIT
