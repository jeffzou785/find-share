# Find Share 项目优化建议：对抗式审查后版本

> 本文基于 `review.md` 的对抗式审查、当前 `README.md` 已完成状态、现有 `src/` 代码结构，以及 `$a-stock-data` 可用数据源重新整理。  
> 核心原则：先修真实瓶颈，再加新指标；先有数据和标签，再谈模型和阈值。

## 0. 当前落地状态快照（2026-07-04）

本文件现在既是优化建议，也是下一轮工具接手开发的路线图。当前状态如下：

已完成：

- `$a-stock-data` A 股补数入口：`AStockSkillSource` + `refresh-skill` 已落地，默认补 2024/2025 年报和 2026Q1，不补 2023。
- 最新缺失池补数：32 只股票财务/估值补齐成功，`financials_full` 入库 33949 行；年报/Q1 PDF 下载与海外收入解析已跑过一轮。
- 最新财报季 run：`20260704_112743_614f_2025A`，策略三 `hit=1`、`watch=6`，策略一主要剩余 `pe_history_missing`。
- 研报 P0 数量门槛：`broker_reports=263`，本地 PDF 元数据 `205`，`research_reports/` PDF 文件 `219`，RAG chunks `2502`。
- 人工标注工具链：`label-export` / `label-import` 已具备，最新标注队列在 `data/exports/human_label_queue.csv`。
- 策略二A 工程 MVP：`pharma-template` / `pharma-vbp` / `pharma-gt` / `pharma-screen` 已具备，标注规则在 `docs/pharma_ground_truth_rulebook.md`。
- 港股/A+H 前置：`global_stock_mappings` 表与 `global-map` 已具备，第一批 5 条 A/H 映射已入库，可继续接 `$global-stock-data`。
- P2 基础验证：评分覆盖率、前瞻收益回测、下一期财务验证、RAG 去重与章节标题均已落地。
- 策略三估值原因码：负 PE/估值缺失已拆为 `valuation_data_missing` / `pe_ttm_invalid`，不再混入 `financial_data_missing`。
- 海外收入 parser 难例第一轮：修复“万吨/亿吨”数量单位误判金额；当前年 0.00 的分地区行不再回退抓上一年金额；策略层低占比 best 候选会从 `candidates_json` 兜底；`001288`/`002145`/`001311`/`002085` 已纳入 golden case 清单。
- 海外收入 F10 fallback：新增纯文本 F10 parser 和可选 mootdx F10 获取；`import_overseas_revenue.py` 在 PDF 失败时默认尝试 F10 主营构成 fallback；`001311`/`002085` 本机实跑 F10 文本为空。

仍需收尾：

- 最新 7 只 `hit/watch` 需要人工回写 `human_label` / `label_reason`。
- 策略二医药仍缺真实 `pharma_vbp_events.csv` 和至少 30 条 `pharma_vbp_ground_truth.csv`。
- 策略一低位判断仍缺历史 PE/PB 序列，腾讯估值快照不能替代 3/5/10 年分位。
- 策略三仍需补 `001311`/`002085` 海外收入：mootdx F10 实跑文本为空，下一步应增强 PDF 表格解析或接其他主营构成源；`001288` 当前仍依赖策略层 candidates_json 兜底，后续可增强分地区表求和。
- 港股扩展池目前只有映射层，尚未把 `$global-stock-data` 行情/财务/新闻字段消费进策略二B。

## 1. 审查结论

原 `improements.md` 的方向大体正确，但存在三类问题：

1. **重复已完成工作**：PE/PB 多窗口分位、新闻数、AI 概念识别、研报覆盖度、screen_runs 快照等已在 README 标注完成，不应继续列为待做主线。
2. **数据基础不足**：当时研报、人工标签、策略二医药专属结构化数据都不足；目前研报数量已补到 P0 门槛，瓶颈转为人工标签和医药 ground truth。
3. **优先级错位**：当前最该先做的是让 P0 审计全绿、修解析难例、补 ground truth，而不是继续叠加字段和数据源。

最终规划应从“加法思维”改为“证据链思维”：

- 每个新增字段必须有消费者。
- 每个新增数据源必须服务某个评分项或风险项。
- 每个阈值必须能通过历史样本或人工标签校准。
- 每个解析器改造必须有失败样本集验证。
- 每轮筛选必须能回答“为什么命中少、为什么剔除、数据缺在哪里”。

## 2. 第一性原理

财报披露后的选股系统，本质是在不完整、滞后且有噪声的数据中，寻找“真实改善但市场未充分定价”的公司。

因此项目后续只围绕六个问题建设：

1. **真实性**：经营改善是否真实，而不是低基数、一次性收益或会计扰动。
2. **低估性**：市场是否仍按旧叙事定价。
3. **持续性**：改善是否能跨越多个报告期。
4. **忽视度**：是否没有被研报、新闻、热点和股价充分反映。
5. **可信度**：核心字段是否有来源、时间、口径和置信度。
6. **可验证性**：筛选结果是否能被人工标注和后续收益/业绩回测验证。

对应的工程原则：

- **先修确定 bug，再做策略扩张。**
- **先建覆盖率报表，再解释命中结果。**
- **先用已扩充研报和 ground truth 校准证据链，再做结构化 claim 抽取。**
- **优先结构化数据源，文本 NLP 只做辅助证据。**
- **不要把没有消费场景的字段提前入库。**

## 3. 对 review.md 的采纳结果

| 建议类型 | 处理 | 原因 |
|---|---|---|
| `compute_risk_penalty` early return bug | 已完成，保留回归测试 | `parse_warning`、现金流、负债风险已累加并封顶 |
| 缺失指标权重重归一问题 | 已完成，保留回归测试 | 缺失子分按中性值处理，并输出 `coverage_ratio` |
| 数据覆盖率报表 | 已完成基础版，继续强化 | `screen_runs.coverage_json` 与 run 目录 `coverage.md` 已落地，后续补字段级解释质量 |
| `hot_reason_count_30d` / `relative_return_60d` | 已完成基础实现 | `NeglectEvidenceCollector` 已提供热点频次和 60 日相对收益，后续验证真实样本稳定性 |
| 日志系统缺失 | 主要流水线已完成，legacy 收尾 | 核心 P0/P2 pipeline 已接入 `src.utils.logging`，少量旧脚本继续收敛 |
| 扩研报数据基础 | 已达 P0 数量门槛 | 当前已有 263 条研报元数据、205 份本地 PDF 元数据、219 个 PDF 文件、2502 个 RAG chunks |
| 人工标签机制 | 工具已完成，标签待回写 | `label-export` / `label-import` 已落地，下一步标注最新 7 只 hit/watch |
| 策略三估值原因码混淆 | 已完成 | `valuation_data_missing` / `pe_ttm_invalid` 已从 `financial_data_missing` 中拆出 |
| 海外收入 parser 难例 | 部分完成 | `002145` 数量单位误判已修，`001288` 由 candidates_json 兜底，F10 fallback 入口已接入；`001311`/`002085` 实跑 F10 文本为空，需增强 PDF 或接其他主营构成源 |
| 消费子行业差异化阈值 | 暂缓 | 当前策略一有效样本不足，且最新 run 主要卡在 `pe_history_missing`，先补历史估值 |
| 行业内分位排序 | 暂缓 | 需要 peer metrics 物化视图，且应先有 reject 分布 |
| PDF 7 步解析流水线 | 降级 | 先做 golden case + F10 fallback，避免低 ROI 重构 |
| 字段 `raw_hash` / 入库 `is_stale` | 暂缓 | 没有明确消费者，先不做死字段 |
| 资金流/龙虎榜/解禁等事件信号 | 暂缓 | 与“财报披露后选股”主线相关性弱，先不污染主策略 |
| 行业研报索引 | 暂缓 | 当前行业研报数据基础不足 |
| §11 P0/P1 时间预算偏乐观 | 采纳 | P0 改为 3-4 周，P1 改为 5-7 周 |
| §11 ground truth rule book | 采纳 | 新增第 11 章，定义主观标签规则 |
| §11 覆盖率字段级定义 | 采纳 | 覆盖率明确为字段级，而不是行级 |
| §11 策略二A PB 分位不应盲删 | 采纳并修正 | PB 分位改为策略二A 软约束 |
| §11 策略二B A 股边界 | 修正 | 新增 `$global-stock-data` 后，MVP 可覆盖 A 股 + 港股行情/财务/新闻/资金对照，但港交所公告全文仍暂缓 |
| §11 暂缓项退出条件 | 采纳 | `10.5` 增加启动 / 退出暂缓条件 |
| `import_overseas_revenue.py` 输出缓冲 | 已完成 | 脚本已接入 logger，后续只需收敛少量 legacy 输出 |
| 港股数据源能力 | 采纳 | 使用 `$global-stock-data` 覆盖港股行情、K线、三表、关键指标、分析师、新闻、资金流和全市场列表 |

### 3.1 对 review 的反驳与保留判断

本版不应机械全盘接受 `review.md`。对抗式审查的价值是暴露风险，但最终规划仍要按策略目标取舍。

保留判断如下：

- **策略二A 不删除 PB 分位**：创新药 PB 天生较高，但策略二A 是集采修复型，主要覆盖仿制药、器械、耗材、IVD，PB 分位仍有低位识别价值。处理方式改为“软约束”：PB 分位偏高进入 watch，不作为硬剔除。
- **策略二B 不再限定纯 A 股**：`$global-stock-data` 已能提供港股行情、K线、财报三表、关键指标、分析师预期、新闻、资金流和全市场列表，因此策略二B 可以纳入港股数据对照；但港交所披露易公告全文仍未接入，涉及 License-out/临床公告时仍要标记公告源缺口。
- **医药外部数据源不压缩工期**：联采办、CDE、FDA/ClinicalTrials 都是独立数据工程，不再把它们塞进 2-4 周的 P1。
- **暂缓项不是删除项**：消费子行业阈值、行业研报索引、CXO/医美独立策略都保留启动条件，避免“暂缓”变成永久搁置。

## 4. 策略一：消费股周期反转

### 4.1 当前定位

策略一已经实现：

- 消费相关行业池。
- PE/PB 多窗口分位。
- 扣非净利润增速。
- 收入增速和毛利率改善。
- 反转判定。

下一步不应先做复杂行业模板，而应先回答：

> 当前为什么有效命中少？最新 run 是数据缺失、阈值过严，还是确实没有更多合格样本？

### 4.2 保留改进

P1 优先加入低成本、高解释性的质量过滤：

- 经营现金流 / 净利润。
- 扣非净利润与归母净利润背离。
- 应收账款增速相对收入增速。
- 存货增速和存货周转变化。
- 销售费用率异常上升。

执行顺序：

1. 先 port 策略三已有的现金流质量逻辑。
2. 再基于新浪三表计算应收、存货、销售费用率。
3. 对每个新增过滤项输出 reject_reason。
4. 有 reject 分布后，再判断是否需要行业差异化阈值。

### 4.3 暂缓改进

以下事项暂缓：

- 消费子行业差异化阈值。
- 行业内分位排序。
- 针对家电、美妆、纺服、社服等单独配置模板。

理由：当前样本太少，且最新 run 主要暴露的是历史估值缺口。正确前置是先补 PE/PB 历史分位、数据覆盖率、reject 分布和人工标签。

## 5. 策略二：医药策略重构

### 5.1 审查结论

原策略二“集采冲击后量价齐升的医药和医疗器械股”抽象层过粗。

在集采语境下，“量价齐升”并不是常态。集采多数时候是**价格下降、销量提升、份额集中**。真正能量价齐升的通常来自：

- 创新产品。
- 海外定价。
- 产品结构升级。

因此策略二不应把整个医药行业塞进一个模型，而应拆成两个子策略。

### 5.2 策略二A：集采修复型

适用范围：

- 化学制剂。
- 中成药。
- 医疗耗材。
- IVD。
- 骨科、心血管等受集采影响较大的器械方向。

核心问题：

> 公司是否曾经受集采冲击，现在价格冲击趋稳，销量和份额恢复，并带来毛利率、现金流或利润趋势改善？

第一性原理：

- 集采不是单纯利空，中标企业可能以价换量并提升份额。
- 早期修复不一定表现为扣非高增，更可能表现为收入恢复、毛利率止跌、现金流改善。
- “集采出清”不能只靠年报话术，要尽量用批次、中标、收入、毛利率等数字证据验证。

建议初筛条件：

- 医药生物相关行业，且属于集采修复适用子行业。
- 非 ST，上市满 3 年，市值不低于 30 亿。
- PE 分位低于 60%。
- PB 分位保留为软约束：低于 70% 加分，高于 70% 进入 watch，不作为硬剔除。
- 过去 8-16 个季度经历过收入、利润或毛利率下行。
- 最新报告期收入同比大于 5%。
- 扣非净利润同比大于 -10%，且连续两期改善。
- 毛利率同比下降不超过 1 个百分点，或环比改善。
- 经营现金流 / 净利润大于 0.6。
- 资产负债率低于 60%。

关键数据：

- 集采批次。
- 中标/落标状态。
- 中标产品数。
- 集采产品收入占比。
- 集采产品收入同比。
- 毛利率同比和环比变化。
- 恢复持续季度数。

风险过滤：

- 商誉 / 归母净资产大于 50%，直接标记高风险。
- 研发资本化率大于 50%，进入 watch。
- 收入增长但应收账款大幅高于收入增速，降权。
- 存货高增且收入未同步改善，降权。

### 5.3 策略二B：创新药/创新器械出海型

适用范围：

- 创新药。
- 生物制品。
- 创新器械。
- 高端影像、手术机器人、AI 辅助诊断等具备海外定价或注册催化的方向。

核心问题：

> 公司是否正在从国内医保约束转向海外商业化、License-out 或创新产品放量，并且市场仍未充分定价？

关键证据：

- License-out 数量和首付款金额。
- FDA/EMA/NMPA/CDE 注册进展。
- 临床试验关键读出。
- 海外收入同比。
- 海外商业化合作方质量。
- 创新产品收入占比。

MVP 边界：

- A 股创新药和创新器械仍是主池。
- 港股标的作为扩展池和 A+H 对照池纳入，优先覆盖港股通、A+H、18A 生物科技和创新器械公司。
- 使用 `$global-stock-data` 获取港股行情、K线、估值、财报三表、关键指标、分析师预期、新闻、资金流和全市场列表。
- 暂不把港交所披露易公告全文视为已解决能力；若关键催化只在港股公告或英文公告中出现，标记 `hk_disclosure_source_gap`。
- A+H 公司同时输出 A 股和 H 股估值、涨跌幅、相对强弱和新闻反应，用于识别港股是否领先定价。

必要新数据源：

- CDE 药品审评数据。
- FDA Drugs@FDA / Orange Book。
- ClinicalTrials.gov。
- 公司公告中的授权协议。
- `$global-stock-data`：港股行情、K线、三表、关键指标、分析师预期、新闻、资金流、全市场列表。

`$a-stock-data` 当前能提供财报、公告、新闻、研报、估值和资金层数据，但医药策略要做扎实，仍需要补充医药专属 ground truth。否则策略二会退化为从财报和研报里抓“出清、放量、修复”等模板话术。

`$global-stock-data` 可以补足港股市场数据和财务数据，但不能替代 CDE/FDA/ClinicalTrials，也不能完全替代港交所披露易公告全文。港股能力应优先用于：

- A+H 估值差和股价反应对照。
- 港股创新药标的 watch pool。
- 港股分析师预期和目标价变化。
- 港股资金流和新闻热度。
- 港股财务指标验证。

### 5.4 策略二落地前置

策略二不能直接从打分模型开始，应先完成四个前置：

1. 建医药行业池：申万一级/二级映射，明确哪些子行业进入二A、二B，哪些暂缓。
2. 建 `pharma_vbp_ground_truth.csv`：至少 30 只历史样本，标注集采批次、修复起点季度、修复持续季度、股价表现、是否真修复。
3. 接入联采办/上海阳光医药采购网数据：用于中标状态和集采批次。
4. 接入 CDE/FDA/ClinicalTrials 数据：用于创新药/器械出海子策略。
5. 建 A 股、港股、A+H 映射表：统一 `A_code`、`HK_code`、`Yahoo_symbol`、`EastMoney_secucode`、`secid_prefix`。

### 5.5 暂缓范围

以下方向不混入策略二主线：

- CXO：单独属于投融资周期和海外订单修复。
- 医药商业：单独属于渠道效率、DTP 和处方外流。
- 医美/消费医疗：单独属于消费周期和监管。

这些方向可以未来新增策略二C/二D，但不应污染集采修复模型。

### 5.6 样本量预估

策略二必须先确认样本量，再决定模型复杂度。

粗略预估：

- **策略二A 集采修复型**：A 股医药生物中，化学制剂、中成药、器械、耗材、IVD 等候选约 180-250 家；非 ST、上市满 3 年、市值大于 30 亿后约 120-180 家；经过低位、修复、现金流过滤后，预计候选 10-25 家。
- **策略二B A 股主池**：纯 A 股创新药/创新器械候选约 20-30 家；具备 License-out、FDA/CDE 关键进展或海外商业化证据的有效样本可能不足 10 家。
- **策略二B 港股扩展池**：港股创新药、18A 生物科技、A+H 和创新器械样本质量更好，可用 `$global-stock-data` 做行情、估值、财务、分析师和新闻验证；但港交所公告全文未接入前，催化剂证据需要标记来源缺口。
- **A+H 对照样本**：用于观察 H 股是否先于 A 股反映 License-out、FDA、临床读出等催化。

决策规则：

- 若策略二A 首轮命中少于 10 家，先放宽硬筛并输出 watch pool，不急于打分。
- 若策略二B A 股有效样本少于 10 家，则先合并港股扩展池作为“研究线索模块”，暂不作为独立量化策略。
- 两个子策略都必须先通过 ground truth 样本校验，再固定阈值。

## 6. 策略三：制造业出海但被 AI 忽视

### 6.1 保留改进

两个“被忽视”核心观测项已经完成基础实现：

- `hot_reason_count_30d`。
- `relative_return_60d`。

下一步不再重复开发字段，而是验证三件事：

1. 最新 `hit/watch` 样本中，这两个字段是否能解释“被市场忽视”。
2. 同花顺热点缓存是否能稳定覆盖财报季候选。
3. 60 日相对收益的基准选择是否需要按行业替代宽基指数。

### 6.2 海外收入同比守卫

`require_overseas_yoy` 已存在，不应无脑打开。

正确逻辑：

- 若 `overseas_revenue` 有至少 2 年数据，则强制计算海外收入同比。
- 若只有 1 年数据，则不硬拒绝，但降低置信度。
- 若解析有 `parse_warning`，风险惩罚应与债务、现金流风险叠加，而不是覆盖。

### 6.3 暂缓改进

以下内容依赖研报结构化和更多数据，先不单独推进：

- 海外客户集中度。
- 关税和汇率风险结构化。
- 产能、订单、客户认证 claim 抽取。

研报数量已经达到 P0 门槛，但这些字段仍应等人工标签、候选级证据去重和 claim 抽取基础设施完成后再做。

## 7. 数据源与数据治理

### 7.1 优先级原则

沿用 `$a-stock-data` 的数据源优先级：

- 行情/估值：优先 mootdx + 腾讯。
- 财务三表：新浪 + mootdx 财务快照兜底。
- 公告/财报 PDF：巨潮。
- 研报：东财个股研报，后续再扩行业研报。
- 热点/关注度：同花顺热点 + 东财新闻 + 概念板块。
- 独有事件数据：东财 datacenter，但只在有明确消费字段时接入。

港股使用 `$global-stock-data`：

- 港股行情：腾讯 `r_hkXXXXX` 为首选，新浪 `rt_hkXXXXX` 和东财 push2 兜底。
- 港股 K 线：Yahoo chart，代码格式如 `0700.HK`。
- 港股财报三表：东财 datacenter，`SECUCODE` 格式如 `00700.HK`。
- 港股关键指标：东财 GMAININDICATOR + Yahoo quoteSummary。
- 港股分析师预期：Yahoo quoteSummary。
- 港股新闻：Yahoo search。
- 港股资金流：东财 push2his，`secid_prefix=116`。
- 港股全市场列表：东财 push2 clist，`market="hk"`。

港股数据的第一批可消费字段：

- `hk_code`
- `hk_name`
- `hk_price`
- `hk_market_cap`
- `hk_pe`
- `hk_pb`
- `hk_revenue_yoy`
- `hk_net_profit_yoy`
- `hk_roe`
- `hk_debt_asset_ratio`
- `hk_analyst_count`
- `hk_target_mean`
- `hk_news_count_30d`
- `hk_relative_return_60d`
- `ah_premium_discount`
- `hk_disclosure_source_gap`

边界说明：

- `$global-stock-data` 解决港股市场和财务数据，不等于已经解决港交所披露易公告全文。
- 对 License-out、临床读出、FDA/EMA 审批等关键催化，仍优先以公司公告、CDE/FDA/ClinicalTrials 或后续港交所公告源验证。

### 7.2 必做数据治理

P0 先做可消费的数据质量字段：

- `source_status`：明确 ok / missing / error / cached / parsed_warning。
- `coverage_json`：每次 run 聚合数据覆盖率和缺失原因。
- `coverage_ratio`：评分项覆盖比例。
- `fetched_at`：必要表的抓取时间。

TTL 分类：

- 行情/估值：1-3 个交易日。
- 财报三表：按报告期和披露更新时间刷新，默认不做短 TTL。
- 研报列表：7-14 天增量刷新。
- 新闻/热点：1-3 天。
- PDF 解析结果：除非 PDF 文件变化或 parser 版本变化，否则不自动过期。

暂缓：

- `raw_hash`。
- 入库字段 `is_stale`。
- 没有消费场景的 `confidence` 泛化字段。

`is_stale` 应优先在读取时由 `fetched_at + TTL` 计算，而不是提前入库。

### 7.3 覆盖率报表

每次财报季筛选必须输出：

- 应处理公司数。
- 成功获取财务数据数。
- 成功获取估值数据数。
- 成功解析海外收入数。
- 成功获取研报数。
- 成功获取热点/新闻数。
- 缺失原因分布。
- reject_reason 分布。
- 需要人工复核样本。

这是解释“策略一为什么只有 4 只、策略三为什么只有 15 只”的前提。

覆盖率必须采用**字段级定义**，而不是行级定义。

建议定义：

- `field_coverage`：某策略硬筛和评分所需字段中，非空且口径有效的字段数 / 应需字段数。
- `hard_filter_coverage`：硬筛字段全部满足可用口径的公司数 / 应处理公司数。
- `score_coverage`：参与评分的字段权重和 / 理论总权重。
- `partial_missing`：数据源返回成功但关键字段缺失，例如财报有记录但 `eps_basic` 为空。
- `insufficient_history`：数据存在但样本量不足，例如 PE 历史不足 `min_history_samples`。

“成功获取财务数据”不能只看某一行记录是否存在，而要看当前策略需要的关键字段是否可用。

### 7.4 已知工程债纳入数据治理

已处理：

- `scripts/import_overseas_revenue.py` 已接入项目 logger，进度输出不再依赖 `python -u`。
- 核心 P0/P2 pipeline 已统一使用 `src.utils.logging.configure_logging`。

仍需收尾：

- 少量 legacy 脚本仍有 `print`，不影响主流程，但后续清理时应统一 logger。
- 数据下载失败时应尽量输出 `source_status` 和可重试命令，减少人工排查成本。

## 8. 财报与研报解析

### 8.1 财报 PDF

不优先做完整 7 步 PDF 解析流水线。

优先顺序：

1. 固化 12 份海外收入解析失败样本。
2. 建 golden case 元数据：代码、报告期、预期值、失败类型。
3. PDF 失败时 fallback 到东财 F10 / mootdx F10 的主营构成。
4. F10 也失败时，再考虑 LLM 或更复杂表格解析。

原因：目标是拿到正确数据，不是证明 PDF parser 足够复杂。

### 8.2 研报数据

研报 P0 数量门槛已经达到，下一步从“多拉研报”转为“让研报真正服务候选解释”。

已完成：

- `broker_reports=263`。
- 本地 PDF 元数据 `205`。
- `research_reports/` 下 PDF 文件 `219`。
- RAG chunks `2502`。

下一步：

- 对最新 7 只 `hit/watch` 建证据包并人工标注。
- 检查 `001231` 等无研报候选是否应保留 watch 或降权。
- 对同一公司重复 PDF / 重复观点做候选级去重，避免 RAG 证据被单家公司研报数量放大。
- EPS Y1/Y2 口径标准化。
- 结构化 claim 抽取。

暂缓：

- 行业研报索引。
- 大规模表格页结构化。
- 用 TF-IDF 直接做复杂 claim 抽取。

## 9. 评分与验证

### 9.1 必修评分问题

已完成：

- `compute_risk_penalty` 中 `parse_warning` 已和债务/现金流风险累加。
- 缺失子分按中性值处理，不再把信息少的公司无条件抬高。
- `ScoreMetrics` 已输出 `coverage_ratio`。
- 策略三中负 PE、估值异常或估值快照不可用已从 `financial_data_missing` 拆出，分别返回 `valuation_data_missing` / `pe_ttm_invalid`。

建议评分语义：

- `final_score`：排序分。
- `coverage_ratio`：评分项覆盖度。
- `risk_penalty`：风险扣分。
- `reject_reason`：硬过滤原因。
- `watch_reason`：降级观察原因。

仍需修正：

- 策略一 `pe_history_missing` 是真实历史估值缺口，不能用腾讯当前快照伪造历史分位。

### 9.2 人工标签

新增人工标签是 P0，不是 P2。

建议在 `candidate_scores` 增加：

- `human_label`：hit / watch / false_positive / false_negative。
- `label_reason`。
- `labeled_at`。

先标注最新 run 的 7 只 `hit/watch` 作为基线，再回溯历史命中样本。标注前优先阅读 `data/exports/latest_candidate_evidence.md`，避免只看分数打标签。

### 9.3 回测

基础入口已完成：

- 20/60/120 日前瞻收益回测。
- 下一期财务验证。

下一步用真实标签校准：

- 20/60/120 日相对行业收益。
- 最大回撤。
- 下一期收入和扣非利润验证。
- 研报覆盖度变化。
- 热点/新闻关注变化。

没有人工标签前，不应把回测结果直接用于调阈值；先做观察和误差归因。

## 10. 执行计划

### 10.1 已完成基线（截至 2026-07-04）

1. 评分层：`risk_penalty` 累加、缺失子分中性填充、`coverage_ratio` 已完成。
2. 覆盖率：`screen_runs.coverage_json`、run 目录 `coverage.md` 已完成。
3. 策略三忽视度：`hot_reason_count_30d`、`relative_return_60d` 已完成基础实现。
4. 日志：核心 P0/P2 pipeline 已接入 `src.utils.logging`。
5. 研报：P0 数量门槛已达，当前 263 条元数据、205 份本地 PDF 元数据、219 个 PDF 文件、2502 个 RAG chunks。
6. 人工标签工具：`label-export` / `label-import` 已完成，最新队列已导出。
7. 策略二A 工程：医药模板、VBP 事件导入、ground truth 校验、`pharma-screen` 已完成。
8. A/H 映射：`global-map`、`global_stock_mappings` 表、首批 5 条映射已完成。
9. P2 验证：20/60/120 日前瞻收益回测、下一期财务验证、RAG 去重和章节标题已完成。
10. `$a-stock-data` 补数：`refresh-skill` 已完成，最新缺失池补数和 2026Q1 PDF 下载已跑通。
11. 策略三原因码：负 PE/估值快照异常/估值历史缺失已拆为 `pe_ttm_invalid` 和 `valuation_data_missing`，并写入 `source_status.extra.valuation_missing_reason`。
12. 海外收入 parser：数量单位误判、当前年 0.00 回退上一年金额、低占比 best 候选兜底与 golden case 清单已完成第一轮。
13. 海外收入 F10 fallback：PDF 解析失败后默认尝试 mootdx F10 主营构成，支持 `--skip-f10-fallback` 关闭；`001311`/`002085` 实跑为空，已确认不能靠当前 mootdx F10 回补。

### 10.2 P0 收尾：让审计全 OK

1. 标注最新 7 只 `hit/watch`：阅读 `data/exports/latest_candidate_evidence.md`，填写 `data/exports/human_label_queue.csv` 的 `human_label` / `label_reason`，再执行 `label-import`。
2. 增强 `001311`/`002085` PDF 表格解析或接其他主营构成源：当前 mootdx F10 实跑为空，不能回补这两个样本。
3. 补策略二 VBP 事件：填写 `data/exports/pharma_vbp_events.csv`，每条必须有 `source_url` 和 `evidence_text`。
4. 补策略二 ground truth：至少 30 条 `data/exports/pharma_vbp_ground_truth.csv`，按 `docs/pharma_ground_truth_rulebook.md` 标注。
5. 重跑 `p0-audit --period 2025A --strategy all`，目标是只剩真实外部数据缺口，最好达到全 OK。
6. 更新 `README.md` 的交接快照：把 P0 audit 状态、最新 run id、剩余 TODO 同步给下一轮工具。

### 10.3 P1：策略质量与数据源增强

1. 策略一历史估值：补真实 PE/PB 历史序列来源，解决当前 12 只消费候选 `pe_history_missing`，不能用腾讯当前快照替代历史分位。
2. 策略一财务质量：在现有经营现金流和扣非质量基础上，增加应收账款、存货、销售费用率，先作为 watch/reject reason 输出。
3. 策略三海外收入 fallback：PDF 解析失败时接东财 F10 / mootdx F10 主营构成，降低 `overseas_revenue_missing`。
4. 策略三研报证据：对无研报候选降权或标记 `research_evidence_missing`，避免“无证据 watch”混入高优先级。
5. EPS 年份标准化：把东财研报 EPS Y1/Y2 转成具体财年，减少与同花顺一致预期的跨年偏差。
6. 缓存 TTL：为必要表补 `fetched_at` 消费逻辑，优先在读取时判断过期，不急于新增 `is_stale` 字段。
7. `$global-stock-data` 港股 adapter：先消费港股行情、K线、三表、关键指标、分析师、新闻、资金流，输出 A/H 对照字段。
8. 策略二B MVP：以 A 股主池 + 港股扩展池 + A/H 对照方式输出研究线索，并显式标记 `hk_disclosure_source_gap`。

### 10.4 P2：验证、校准与结构化研究

1. 用人工标签和前瞻收益回测校准策略一/策略三阈值。
2. 用下一期财务验证校准“高增是否兑现”，重点看扣非净利、收入、现金流。
3. 在 200 篇以上研报基础上做轻量 claim 抽取，先抽海外订单、产能、客户、License-out、FDA/CDE 进展。
4. 建候选级证据去重：同一公司多篇研报重复观点不能线性放大置信度。
5. 评估消费子行业差异化阈值：只有当 watch pool 和人工标签样本足够时再拆行业模板。
6. 评估 peer metrics：确认绝对阈值误杀明显后，再做行业内分位排序。

### 10.5 暂缓 / 删除

| 事项 | 处理原因 | 启动 / 退出暂缓条件 |
|---|---|---|
| 消费子行业差异化阈值 | 当前样本不足，先看 reject 分布 | 消费候选命中或 watch pool ≥15 只，且某子行业被同一原因剔除比例 >50%，且该子行业有 ≥5 只人工标签样本 |
| 行业内分位排序 | peer metrics 视图未就绪，且不是当前瓶颈 | 建成 peer metrics 物化视图，且已有覆盖率报表证明绝对阈值误杀明显 |
| PDF 7 步全量流水线 | 先用 golden case + F10 fallback | F10 fallback 后海外收入失败率仍 >5%，且失败样本影响策略三候选排序 |
| 行业研报索引 | 行业研报数据基础不足 | 个股研报 ≥200 篇稳定后，再累计行业研报 ≥50 篇并验证能提升候选解释力 |
| 资金流/龙虎榜/大宗/解禁纳入主策略 | 偏事件交易，暂不污染财报主线 | 回测证明事件信号能显著改善 20/60 日风险收益，且不降低财报策略可解释性 |
| 港交所披露易公告全文 | `$global-stock-data` 已覆盖港股行情/财务/新闻，但不覆盖公告全文 | 策略二B 港股扩展池跑通后，若 `hk_disclosure_source_gap` 影响超过 20% 候选的关键催化验证，则接入港交所公告源 |
| CXO 策略二C | 与集采修复逻辑不同 | 策略二A/二B 跑通后，若 CXO watch pool ≥10 只且可获得订单/投融资周期指标 |
| 医药商业/医美策略二D | 与集采修复逻辑不同 | 策略二A/二B 跑通后，若能定义独立可验证的渠道/消费复苏指标 |
| `raw_hash` / 入库 `is_stale` | 没有明确消费者 | 出现数据重复抓取审计或 parser 版本追踪需求后再做 |

## 11. Ground Truth 标注 Rule Book

策略二 ground truth 必须先有标注规则，再有 CSV。建议将规则保存为 `docs/pharma_ground_truth_rulebook.md`，CSV 保存为 `data/exports/pharma_vbp_ground_truth.csv`。

### 11.1 字段定义

建议最小字段：

- `code`
- `name`
- `sub_strategy`：vbp_recovery / innovation_export
- `sub_industry`
- `vbp_batch`
- `vbp_status`：won / lost / not_applicable / unknown
- `shock_start_quarter`
- `recovery_start_quarter`
- `recovery_quarter_count`
- `recovery_basis`
- `price_performance_window`
- `relative_return`
- `human_label`
- `label_reason`
- `label_version`

### 11.2 修复起点季度

`recovery_start_quarter` 按优先级判断：

1. 收入同比由负转正，且毛利率同比下降幅度收窄。
2. 扣非净利润同比连续两期改善。
3. 经营现金流 / 净利润改善到 0.6 以上。
4. 财报或公告披露集采产品收入、销量或中标放量的具体数字。

若四项都没有，仅有“集采影响逐步出清”等话术，不允许标为明确修复起点，只能标 `watch`。

### 11.3 修复持续季度数

从 `recovery_start_quarter` 开始，连续满足以下至少两项的季度数：

- 收入同比为正。
- 扣非净利润同比改善。
- 毛利率同比降幅收窄或环比改善。
- OCF/净利润不恶化。
- 应收账款增速不显著高于收入增速。

若中途出现两项以上恶化，则修复连续性中断。

### 11.4 是否真修复

`human_label` 建议规则：

- `hit`：财务改善连续至少 2 个季度，且有数字证据支持集采出清、产品结构改善或创新/出海催化。
- `watch`：财务改善刚出现，但证据不足或存在明显风险。
- `false_positive`：收入或利润改善来自一次性因素、低质量应收、非经常性收益或模板话术。
- `false_negative`：规则未命中但人工确认存在真实修复或催化。

### 11.5 标注一致性

建议：

- 每只样本至少保留 `label_reason`。
- 规则版本写入 `label_version`。
- 若多人标注，分歧样本单独列入 `needs_adjudication`。
- 修改历史样本标签时，保留旧标签和修改原因。

## 12. 最小可交付目标

下一阶段的成功标准不是“新增了多少指标”，而是把证据闭环真正跑通：

1. 已完成：每次 screen 都能看到覆盖率和缺失原因。
2. 已完成：每个候选都有 score、coverage_ratio、risk_penalty、reject/watch reason。
3. 已完成：策略三的忽视度证据不再是占位字段。
4. 已完成：研报数据达到可支撑 RAG 的 P0 规模。
5. 已完成：策略二有医药行业池、工程入口、ground truth rule book 和 A/H 映射前置。
6. P0 收尾：最新命中样本有人工标签。
7. P0 收尾：策略二有至少 30 条 ground truth 和真实 VBP 结构化事件。
8. P1 后续：策略二B 能输出港股扩展池和 A/H 对照字段，并明确标记 `hk_disclosure_source_gap`。

做到这些后，再继续加阈值、加 claim、加行业模板才有意义。
