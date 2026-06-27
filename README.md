# find-share

A 股选股框架：基于公开数据的量化初筛 + 研报深度分析的两阶段工作流。

## 已实现的两大策略

| 策略 | 逻辑 | 当前命中 |
|------|------|---------|
| **消费股反转** | 食品饮料/家电/美容护理/商贸零售/纺服/社会服务/轻工 + PE 分位<30%（**3y/5y/10y 可选**）+ 扣非同比>30% + **反转判定**（业绩拐点 OR 趋势验证）| **4 只** |
| **出海隐形冠军** | 机械/汽车/化工 + 海外收入占比 30%~95% + PE<25 + **现金流/净利≥0.7** + **资产负债率<60%** + 海外同比>40%（可选）+ **被忽视证据链**（研报/新闻/概念聚合）| **15 只** + 50 只扩展池候选 |

> 策略二（医药量价齐升）已放弃。

## 功能模块

### 数据采集层 `src/collectors/`

| 模块 | 功能 | 文件 |
|------|------|------|
| `AkShareSource` | 7 个核心接口（股票列表/行业/PE 历史/财报/披露日历），带重试 + 24h 缓存 | `akshare_impl.py` |
| `LocalCachedSource` | **P1.5-1**：装饰 DataSource，先读 DuckDB `pe_pb_history`/`financials`，缺失才 fallback + 回写 | `cached_impl.py` |
| `NeglectEvidenceCollector` | **P1.5-3**：东财个股新闻 + AI/半导体概念识别 + 被忽视证据聚合 | `neglect_evidence.py` |
| `SinaFinancialSource` | 新浪财报三表（利润表/资产负债表/现金流量表），70+ 科目中英映射 | `sina_impl.py` |
| `EastMoneyResearchSource` | 东财研报列表（评级 + 三年 EPS 预测）+ PDF 下载（pdf.dfcfw.com 带 Referer 鉴权）| `eastmoney_research.py` |
| `ThsForecastSource` | 同花顺一致预期 EPS（独立口径，用于交叉验证东财）| `ths_forecast.py` |
| `em_get` | 东财统一限流（≥1s 间隔 + 随机抖动 + Session 复用 + 跳过 Clash 代理）| `_em_throttle.py` |
| `CnInfoDownloader` | 从巨潮下载定期报告 PDF（年报/半年报/一季报/三季报四类通用）| `cninfo_downloader.py` |
| `EmWebClient` | 东财 F10 拿全市场行业映射（5207/5527 = 94.2%）| `emweb_client.py` |
| `annual_report_parser` | 年报 PDF 提取境外收入（单位识别 + 跨页去重 + 合理性校验 + **P1.5-2 多 high 候选取小值**）| `annual_report_parser.py` |
| `DataSource` Protocol | 抽象层，未来切 Tushare 时业务代码零修改 | `base.py` |

### 存储层 `src/storage/`
DuckDB 13 张表：
- 静态数据：`stocks` / `industry_first` / `industry_second` / `stock_industry`
- 估值/财务：`pe_pb_history` / `financials` / `financials_full`（新浪三表长格式）
- 研报：`broker_reports`（东财研报列表）/ `eps_forecast_consensus`（同花顺一致预期）
- 其他：`overseas_revenue`（年报附注境外收入）/ `disclosures`（披露日历）
- **审计（P0）**：`screen_runs`（运行级）/ `candidate_scores`（个股级）

全部带 upsert，重复入库保留 `pdf_path` / `ingested_to_rag` 状态。

### 指标层 `src/indicators/`
- `valuation.py`：PE/PB 历史分位（**P1.5-4：参数化 3y/5y/10y 窗口**）
- `growth.py`：扣非净利润 TTM 计算 + 同比增速

### 策略层 `src/strategies/`
- `consumer_reversal.py`：策略一 + 反转判定 + P1.5-4 行业覆盖扩展（含化妆品/医美/纺服/服务消费/轻工）+ PE/PB 分位窗口参数化
- `overseas_champion.py`：策略三 + 3 个扩展过滤 + P1.5-2 多 high 候选 ratio 校验（candidates_json 兜底）+ P1.5-3 被忽视证据链
- `filters.py`：质量/流动性过滤（剔除 ST/金融/地产/低市值/极端 PE）

### 研报知识层 `src/knowledge/`
jieba 分词 + TF-IDF + SQLite 实现的轻量 RAG（不依赖 chromadb，秒级启动）。
- `index_directory(pdf_dir)` 批量索引
- `ingest_pdf(pdf_path, metadata)` 单文件入库（含显式 metadata 覆盖文件名解析）
- `query(question, top_k)` TF-IDF 余弦相似度检索
- **P1.5-5 同义词扩展**：查询自动扩展同义词组（海外/境外/国外/出口/国际，订单/中标/签约 等 10 组），解决"海外 vs 境外"召回率问题

### Pipeline 编排 `src/pipeline/` + `scripts/`
**P2-0 统一 CLI**：`python3 -m src.pipeline.cli <subcommand>`，封装 12 个独立脚本。
12 个可独立运行的脚本，覆盖 bootstrap → 缓存预热 → 策略筛选 → 研报拉取 → RAG 检索 全流程。

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

# 预热本地财务/估值数据（P1.5-1，让策略跑得更快更稳）
python3 -m src.pipeline.cli refresh --limit 30

# 跑策略（统一入口）
python3 -m src.pipeline.cli strategy1                              # 策略一：消费反转
python3 -m src.pipeline.cli strategy3                              # 策略三：出海隐形冠军

# 财报季主入口（状态化，落 screen_runs/candidate_scores 审计表）
python3 -m src.pipeline.cli screen --period 2025A --strategy all --limit 30
python3 -m src.pipeline.cli screen --period 2025A --strategy overseas --enable-neglect-evidence  # 开被忽视证据链

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
# 全候选池预热 PE/PB 历史 + 财务摘要到本地 DuckDB
python3 scripts/refresh_financials_and_valuation.py

# 只跑指定股票 / 限制数量 / 按行业过滤
python3 scripts/refresh_financials_and_valuation.py --codes 600031 601058
python3 scripts/refresh_financials_and_valuation.py --limit 30
python3 scripts/refresh_financials_and_valuation.py --industries 机械设备 食品饮料

# 强制覆盖本地已有数据
python3 scripts/refresh_financials_and_valuation.py --force
```

预热后，`run_after_disclosure.py` / `run_phase2_strategy1.py` / `run_phase3_strategy3.py`
通过 `LocalCachedSource` 自动从本地读，缺失才 fallback AkShare + 回写。

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

### P0 - ✅ 已完成

| 阶段 | 完成内容 |
|------|---------|
| **海外收入数据覆盖**（2026-06-15）| 56 只候选下载 2024 + 2025 年报，入库 93 条境外收入记录；策略三从 6 → 24 只 |
| **数据合理性过滤**（2026-06-15）| `overseas_champion.py` 加 `overseas_ratio<0.95` + 同比异常剔除 |
| **数据源扩充 Phase 1**（2026-06-19）| 新浪财报三表（`sina_impl.py`）+ cninfo 季报/半年报支持 |
| **数据源扩充 Phase 2**（2026-06-19）| 东财研报 + 同花顺一致预期 + DuckDB 3 张新表（`financials_full`/`broker_reports`/`eps_forecast_consensus`）+ RAG `ingest_pdf` |
| **策略精度提升**（2026-06-19）| 策略一加反转判定（24→4 只真反转）；策略三加现金流/负债率过滤（24→15 只）+ 一致预期可选过滤 |
| **P0 状态化改造 + 事件驱动入口**（2026-06-27）| screening 抽象层（Status / ScreeningResult / MetricsSchema）+ screen_runs/candidate_scores 审计表 + `run_after_disclosure.py` + baseline_diff.py + 161 测试覆盖 |
| **状态化改造修复**（2026-06-27）| 修 `run_overseas_champion` dup `_evaluate_one` 导致旧 CSV 入口不走状态化路径（P1-2/P1-3 增强失效）；修 resume 多策略 fingerprint 不一致；修 AkShare `revenue_yoy`/`gross_margin` 百分数单位；`cleanup_stale_screen_runs` 改用 INTERVAL 字面量 |

### P1 - ✅ 已完成（2026-06-27）

| 功能 | 完成内容 |
|------|---------|
| **P1-3 海外收入解析增强**| 总营收/合计行标记 + 同页境内+境外判定置信度 + 跨页 `\n` 修复 + `select_best_record` 优先高置信度 + 多年交叉校验 + 候选保留到 `candidates_json` + `overseas_revenue` 表扩 3 列 |
| **P1-5a 数据源 baseline 工具**| 30-50 只样本股的 AkShare vs 新浪对照 + 差异分布表 + 不可替换字段清单 + 单源缺失统计 |
| **P1-1 策略一新信号**| PB 5 年分位 + 营收同比 + 毛利率同比改善验证 + 数据缺失降级 watch 不直接剔除 |
| **P1-2 策略三"被忽视"信号**| 研报覆盖度 `reports_count_90d` 填入 metrics.catalyst；不改硬过滤 |

### P1.5 - ✅ 已完成（2026-06-28）

| 功能 | 完成内容 |
|------|---------|
| **P1.5-1 财务/估值本地落库**（`cached_impl.py` + `refresh_financials_and_valuation.py`）| `LocalCachedSource` 装饰 AkShare：先读 DuckDB `pe_pb_history`/`financials`，缺失才 fallback + 回写；新增批量预热脚本；策略代码透明切换 |
| **P1.5-2 海外收入困难样本**（`annual_report_parser.py` + `overseas_champion.py`）| `select_best_record` 多 high 候选 max/min > 5x 时取**最小**（避免误抓总营收，600690 案例）+ 策略层 `_pick_plausible_candidate` 从 `candidates_json` 选合理 ratio + 关键词扩展（美洲/欧洲/亚洲等区域名词） |
| **P1.5-3 被忽视证据链**（`neglect_evidence.py`）| `NeglectEvidenceCollector`：东财个股新闻数（`news_count_30d`）+ AI/半导体概念识别（`is_ai_related`）+ 热点/相对收益占位 + `compute_neglect_evidence` 聚合可读证据；不改硬过滤，仅填 metrics.catalyst |
| **P1.5-4 策略一行业覆盖+窗口参数化**（`consumer_reversal.py`）| `TARGET_INDUSTRIES` 扩展（化妆品/个护/医美/纺服/服务消费/轻工）+ `SUPPORTED_HISTORY_WINDOWS = (3, 5, 10)` + PE/PB 分位三窗口全填入 metrics + `history_years` 校验 |
| **P1.5-5 RAG 语义质量升级**（`research_rag.py`）| 同义词词典（10 组：海外/境外/出口、订单/中标/签约、欧洲、东南亚 等）+ `expand_query_synonyms` 自动扩展查询词 + 单元测试覆盖 |
| **P1.5-6 legacy CSV 字段补全**（`overseas_champion.py`）| `_result_to_legacy_dict` 补齐 `ocf_net_yi`/`net_profit_yi`/`total_liabilities_yi`/`total_assets_yi`/`eps_current`/`eps_forecast_y1/y2`/`eps_y1/y2_growth` 9 个字段，状态化后信息量不降级 |

### P2 - 部分完成

| 功能 | 状态 |
|------|------|
| **P2-0 统一 CLI** ✅（2026-06-28）| `python3 -m src.pipeline.cli <subcommand>`，封装 12 个脚本（bootstrap/refresh/screen/strategy1/strategy3/reports/pdf/rag/baseline 等）|
| P2-1 评分层 | 等 `candidate_scores` 稳定后做线性模型 |
| P2-2 半年报/季报扩展 | 待做 |
| P2-3 动态监控 | 未实现 |
| P2-4 财报 vs 研报一致性校验 | 未实现 |
| P2-5 Tushare 兜底 | Protocol 已就位，待数据源不稳时切换 |

### 已知问题

- 海外收入多 high 候选时，P1.5-2 已自动取最小值并标 parse_warning；策略层 candidates_json 兜底已落地，但极端 case 仍可能漏过（需配合人工 review）
- 海外收入解析仍有 12 份 PDF 找到附注但未提取到境外行（pdfplumber 表格结构识别限制；P1.5-2 已加区域名词扩展）
- `neglect_evidence.py` 中 `hot_reason_count_30d` 和 `relative_return_60d` 为占位实现，待接入行情/热点数据源
- 没有日志系统，全用 print
- `import_overseas_revenue.py` 不带 `-u` 时 print 被 pipe 缓冲，看不到实时进度
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

### 4. AkShare 接口偶发失败

**症状**：`stock_value_em` 等接口偶发返回空。

**解决**：已用 `@akshare_call` 装饰器做了 3 次指数退避重试 + 24h pickle 缓存。仍失败时可加 `force_refresh=True` 跳过缓存。

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
│   │   ├── akshare_impl.py          # AkShare 主实现（7 接口）
│   │   ├── cached_impl.py           # P1.5-1 LocalCachedSource
│   │   ├── neglect_evidence.py      # P1.5-3 被忽视证据 collector
│   │   ├── sina_impl.py             # 新浪财报三表
│   │   ├── eastmoney_research.py    # 东财研报 + PDF
│   │   ├── ths_forecast.py          # 同花顺一致预期
│   │   ├── _em_throttle.py          # 东财统一限流
│   │   ├── emweb_client.py          # 东财 F10 行业映射
│   │   ├── cninfo_downloader.py     # 巨潮定期报告 PDF
│   │   ├── annual_report_parser.py  # 年报境外收入解析
│   │   ├── industry_mapping.py      # 申万↔新浪行业映射
│   │   └── _retry.py                # @akshare_call 装饰器
│   ├── storage/                     # DuckDB 持久化（13 张表）
│   ├── indicators/                  # PE/PB 分位（3y/5y/10y）+ TTM 增速
│   ├── strategies/                  # 策略一/三 + 过滤器 + 反转判定
│   ├── knowledge/                   # 研报 RAG（jieba+TF-IDF+SQLite+同义词扩展）
│   ├── screening/                   # 状态化抽象层（Status/ScreeningResult/MetricsSchema）
│   └── pipeline/                    # 编排 + 统一 CLI
├── scripts/                         # 12 个可独立运行的脚本
├── data/
│   ├── duckdb/                      # 主数据库（不入库）
│   ├── pdfs/                        # 原始年报 PDF（不入库）
│   ├── cache/                       # AkShare/Sina/RAG 缓存（不入库）
│   └── exports/                     # CSV/MD 结果输出
└── research_reports/                # 研报文件夹（含 broker/ 子目录，不入库）
```

---

## 升级路径

| 触发条件 | 升级动作 | 工作量 |
|---------|---------|--------|
| AkShare 接口连续 3 天失败率>20% | 切 Tushare 2000 积分（一次性约 2000 元）| 写 `tushare_impl.py`，业务代码零修改，约 2-3 天 |
| 需要盘中实时监控 | 加 websocket 行情接入 | 接东财/雪球推送，1-2 天 |
| 研报量 > 100 篇 | 升级 RAG 到 embedding | metadata + 同义词 + 模板已落地，外部 embedding API 是下一步，本地模型最后评估 |
| 需要全市场一致预期过滤 | 批量跑 `import_research_reports.py` 后开 `require_consensus_growth=True` | 数据拉取 ~30 分钟 |
| 转专业投资者 | 上 Wind/iFinD | API 接入，1-2 周 |

---

## License

MIT
