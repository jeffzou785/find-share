# find-share

A 股选股框架：基于公开数据的量化初筛 + 研报深度分析的两阶段工作流。

## 已实现的两大策略

| 策略 | 逻辑 | 当前命中 |
|------|------|---------|
| **消费股反转** | 食品饮料/家电/美容护理/商贸零售 + PE 分位<30% + 扣非同比>30% | 24 只 |
| **出海隐形冠军** | 机械/汽车/化工 + 海外收入占比>30% + PE<25（2025 年报基准 + 2024 同比）| **24 只命中** + 50 只扩展池候选 |

## 9 大功能模块

### 数据采集层 `src/collectors/`

| 模块 | 功能 | 文件 |
|------|------|------|
| `AkShareSource` | 7 个核心接口（股票列表/行业/PE历史/财报/披露日历），带重试 + 24h 缓存 | `akshare_impl.py` |
| `CnInfoDownloader` | 从巨潮资讯网下载年报 PDF | `cninfo_downloader.py` |
| `EmWebClient` | 东财 F10 拿全市场行业映射（5207/5527 = 94.2%）| `emweb_client.py` |
| `annual_report_parser` | 年报 PDF 提取境外收入（单位识别 + 跨页去重 + 合理性校验，100% 成功率）| `annual_report_parser.py` |
| `DataSource` Protocol | 抽象层，未来切 Tushare 时业务代码零修改 | `base.py` |

### 存储层 `src/storage/`
DuckDB 8 张表：stocks / industry_first / industry_second / stock_industry / pe_pb_history / financials / overseas_revenue / disclosures，全部带 upsert。

### 指标层 `src/indicators/`
- `valuation.py`：PE/PB 5 年历史分位
- `growth.py`：扣非净利润 TTM 计算 + 同比增速

### 策略层 `src/strategies/`
- `consumer_reversal.py`：策略一
- `overseas_champion.py`：策略三
- `filters.py`：质量/流动性过滤（剔除 ST/金融/地产/低市值/极端 PE）

### 研报知识层 `src/knowledge/`
jieba 分词 + TF-IDF + SQLite 实现的轻量 RAG（不依赖 chromadb，秒级启动）。当前 14 篇研报 / 117 chunks 已入库。

### Pipeline 编排 `src/pipeline/` + `scripts/`
8 个可独立运行的脚本，覆盖 bootstrap → 策略筛选 → 研报检索 全流程。

---

## 快速开始

### 环境要求

- Python 3.9+
- 网络可访问 `push2.eastmoney.com`（建议关 Clash Verge 或加白名单）

### 安装

```bash
# 克隆
git clone git@github.com:jeffzou785/find-share.git
cd find-share

# 装依赖（pip 配置见下文「踩坑指南」）
pip install -r requirements.txt
# 或
pip install akshare pandas numpy duckdb pdfplumber requests tenacity python-dotenv tqdm pyarrow jieba
```

### 配置

```bash
cp .env.example .env
# 默认配置即可运行，无需修改
```

### 三条主命令

```bash
# 1. 初始化基础数据（首次运行，约 5 分钟）
python3 src/pipeline/bootstrap.py                              # 全市场股票列表
python3 scripts/bootstrap_emweb_industry.py                    # 全市场行业映射（94% 覆盖率）

# 2. 跑策略一（消费反转）
python3 scripts/run_phase2_strategy1.py
# → 输出 data/exports/target_pool.csv（24 只命中）

# 3. 跑策略三（出海隐形冠军）
python3 scripts/run_phase3_strategy3.py
# → 输出 data/exports/target_pool_overseas.csv（24 只命中 + 50 只扩展池）
```

---

## 完整工作流

### 日常使用（数据已就绪）

```bash
# 跑策略
python3 scripts/run_phase2_strategy1.py
python3 scripts/run_phase3_strategy3.py

# 研报检索
python3 scripts/research_rag_cli.py search "海外业务增速 出口"
python3 scripts/research_rag_cli.py search --stock 600031 "海运费 汇率"
python3 scripts/research_rag_cli.py info                # 查看 RAG 状态
```

### 数据维护（按需）

```bash
# 刷新行业映射（财报季、IPO 多时跑）
python3 scripts/bootstrap_emweb_industry.py

# 扩展策略三候选池：批量下载扩展池中 PE<25 的股票年报
python3 scripts/download_annual_reports.py --extension --limit 50

# 入库新下载的年报
python3 scripts/import_overseas_revenue.py

# 重新跑策略三（新数据生效）
python3 scripts/run_phase3_strategy3.py

# 索引新研报（把新研报 PDF 放到 research_reports/ 后跑）
python3 scripts/research_rag_cli.py index
```

### 查看结果

```bash
cat data/exports/target_pool.csv                       # 策略一 24 只
cat data/exports/target_pool_overseas.csv              # 策略三 24 只
cat data/exports/overseas_extension_candidates.csv     # 策略三扩展池 50 只
open data/exports/strategy1_result.md
open data/exports/strategy3_result.md
```

### 找机械行业出海标的（完整示例）

```bash
# 1. 先看现有命中
cat data/exports/target_pool_overseas.csv
# → 已有 24 只（三一、潍柴、中联、玲珑、杰克、福耀等）

# 2. 看扩展池候选（PE<25 但年报未入库的 50 只）
head -20 data/exports/overseas_extension_candidates.csv

# 3. 挑感兴趣的（如赛轮轮胎、宇通客车），下载年报
python3 scripts/download_annual_reports.py 601058 600066 600761

# 4. 入库并重跑
python3 scripts/import_overseas_revenue.py
python3 scripts/run_phase3_strategy3.py

# 5. 对感兴趣的标的做研报分析
python3 scripts/research_rag_cli.py search "宇通客车 海外订单" --stock 600066
```

---

## 待升级项（按优先级）

### P0 - ✅ 已完成（2026-06-15）

| 功能 | 完成内容 |
|------|---------|
| **海外收入数据覆盖** | 56 只候选全部下载 2024 + 2025 年报，入库 47 + 46 = **93 条记录**，覆盖 56 只股票 |
| **海外收入同比增速** | 同一只股票同时入库 2024 + 2025，**43 只有同比数据**；策略三从原 6 只命中扩展到 **24 只** |
| **cninfo 标题变体兼容** | `cninfo_downloader.py` 同时识别 `{year}年年度报告`（多数）与 `{year}年度报告`（如风神轮胎）|
| **策略层数据合理性过滤** | `overseas_champion.py` 加 `overseas_ratio_max=0.95`（剔除抓成总营收）+ `sanity_check_yoy`（同比 \|yoy\|>5 或 <-80% 视为单位识别错）|

### P1 - 提升精度

| 功能 | 当前状态 | 升级路径 |
|------|---------|---------|
| **`annual_report_parser` 单位识别** | 部分公司单位识别错（如 600262 元→万元、001333 万元→元）| 改用"同股票多年数据交叉校验"自动修正单位 |
| **PDF 跨页数字断字** | 如 601233 桐昆股份（74 亿被截成 0.07 亿）| 解析器加跨页连接逻辑 |
| **多候选记录优选** | 部分年报取到总营收而非境外收入（000680、603969）| 加上下文词过滤（"营业收入"/"主营业务收入"排除）|
| **`financials` / `pe_pb_history` 表落库** | 仍为空（每次跑策略实时拉 AkShare）| bootstrap 阶段入库，减少网络依赖 |
| **研报 RAG 语义质量** | TF-IDF 关键词匹配 | 装 sentence-transformers + bge-small-zh（~100MB 模型）|
| **策略一行业分类** | "美容护理"覆盖不全 | 手工建成分股清单 |
| **PE 分位时间窗口** | 固定 5 年 | 给 `StrategyConfig` 加 `history_years` 参数 |

### P2 - 锦上添花

| 功能 | 状态 |
|------|------|
| 动态监控（Phase 5） | 未实现 |
| 财报 vs 研报一致性校验 | 未实现 |
| CLI 统一入口 | 每个脚本独立 |
| Tushare 升级 | Protocol 已就位，待数据源不稳时切换 |

### 已知问题

- 年报跨页同金额数据未合并（如比亚迪 2 条不同口径境外收入）
- 海外收入合理性校验仅做"超过 5 万亿则除 1 万"，不够智能；策略三已加 ratio<0.95 + 同比异常过滤兜底，但金额绝对值错的少数股票（如 600262 单位识别错）仍可能漏过
- 没有日志系统，全用 print
- `import_overseas_revenue.py` 不带 `-u` 时 print 被 pipe 缓冲，看不到实时进度

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

**解决**：本框架已用 `emweb.eastmoney.com`（F10 接口）替代，无需处理。

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

---

## 项目结构

```
find-share/
├── src/
│   ├── collectors/         # 数据采集层
│   │   ├── base.py         # DataSource Protocol
│   │   ├── akshare_impl.py # AkShare 主实现
│   │   ├── emweb_client.py # 东财 F10 行业映射
│   │   ├── cninfo_downloader.py
│   │   ├── annual_report_parser.py
│   │   └── industry_mapping.py
│   ├── storage/            # DuckDB 持久化
│   ├── indicators/         # PE/PB 分位 + TTM 增速
│   ├── strategies/         # 策略一/三 + 过滤器
│   ├── knowledge/          # 研报 RAG
│   └── pipeline/           # 编排
├── scripts/                # 8 个可独立运行的脚本
├── data/
│   ├── duckdb/             # 主数据库（不入库）
│   ├── pdfs/               # 原始年报 PDF（不入库）
│   ├── cache/              # AkShare/RAG 缓存（不入库）
│   └── exports/            # CSV/MD 结果输出
└── research_reports/       # 研报文件夹（不入库）
```

---

## 升级路径

| 触发条件 | 升级动作 | 工作量 |
|---------|---------|--------|
| AkShare 接口连续 3 天失败率>20% | 切 Tushare 2000 积分（一次性约 2000 元）| 写 `tushare_impl.py`，业务代码零修改，约 2-3 天 |
| 需要盘中实时监控 | 加 websocket 行情接入 | 接东财/雪球推送，1-2 天 |
| 研报量 > 100 篇 | 升级 RAG 到 embedding | sentence-transformers + bge-small-zh，0.5 天 |
| 转专业投资者 | 上 Wind/iFinD | API 接入，1-2 周 |

---

## License

MIT
