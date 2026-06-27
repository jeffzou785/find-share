# find-share 改进文档 Review（v5）

针对 `FINANCIAL_REPORT_SCREENING_IMPROVEMENTS.md` v3（README 同步版，2026-06-27）的复核意见。

## 文档状态

- v3 把 P0/P1 已完成项和 P1.5/P2 待做项清晰分离，跟 README 对齐做得好。
- v3 比旧版（v2，1069 行）精简到 373 行，去掉了与 README 重复的执行计划细节。
- v5 相比 v4 的变化：
  - v4 主要复核旧 v2 的执行计划，已经不再适用（v2 的执行计划全部完成）。
  - v5 重新基于 v3 + 今天的状态化改造修复（commit `d6ac629`）做复核。
- 所有代码引用位置已于 2026-06-27 重新核查。

---

## 一、已采纳（v3 已经吸收）

### 1.1 P0/P1 完成状态对齐 ✓

v3 第 23-32 行明确：
- P0 财报季闭环已基本跑通（screening 抽象层 + 审计表 + run_after_disclosure + baseline diff + 测试）。
- P1 基础信号已部分落地（PB/营收/毛利率/研报覆盖度/parser 增强/baseline 工具）。
- 剩余问题集中在 P1.5 和 P2。

跟当前代码状态完全对齐。

### 1.2 状态语义沿用 P0 ✓

v3 §4.1 保留 `hit / watch / rejected / data_missing / error` 五状态语义，并明确：
- `parse_warning` 优先 `watch`，不直接 `rejected`。
- 软证据（一致预期、研报覆盖、热点）缺失时不剔除。

跟 `src/screening/status.py` 实现一致。

### 1.3 数据源分层 + 暂不新增表 ✓

v3 §4.2 / §4.3 保留：
- AkShare / 新浪 / 巨潮 / 东财 / 同花顺 分层主源 + 辅助源。
- 短期不新增 `financial_snapshots` / `report_events`。

跟今天代码（11 张 DuckDB 表，含 `screen_runs` / `candidate_scores`）一致。

### 1.4 stale run 清理修复 ✓

v3 §3.2 第 62 行明确："`cleanup_stale_screen_runs` 已实现并修复 DuckDB interval 写法"。

对应 `src/storage/duckdb_store.py:598`：今天把 `pd.Timedelta(hours=...)` 当 SQL 参数改成 `INTERVAL '{n}' HOUR` 字面量 + int 校验。

---

## 二、🔴 必须立刻修（今天已修，但 v3 还没记录）

### 2.1 run_overseas_champion 重复 _evaluate_one（已修）

**问题**：commit `d09bcd5` 之前，`src/strategies/overseas_champion.py` 有两个 `_evaluate_one` 定义：
- line 431：新写的状态化 shim（转发到 `_evaluate_one_to_result`）
- line 556：旧的内联实现（保留原样）

Python 后定义覆盖前定义，导致 `run_overseas_champion` 调的是旧实现，**完全不走状态化路径**。后果：
- `scripts/run_phase3_strategy3.py` 跑的是旧行为，没拿到 P1-2 / P1-3 增强。
- README 宣称"策略三加现金流/负债率过滤"，但旧 CSV 入口实际没生效。
- line 431 的 shim 是死代码，而且签名缺 `overseas_meta` 参数，调用会 `TypeError`。

**修复**：删除 line 556 旧实现，重构 `run_overseas_champion` 调 `evaluate_overseas_full` + 过滤 hit。`_result_to_legacy_dict` 补 `ocf_to_profit` / `debt_ratio` 字段。

**测试**：`tests/strategies/test_overseas_champion.py` 加 5 个直接调 `run_overseas_champion` 的回归测试（hit / parse_warning / yoy 异常 / PE 过高 / 行业不符）。

**v3 文档建议补充**：§3.2 加一行说明此修复。这是 P0 级 bug，比 stale run interval 修复严重得多。

### 2.2 run_phase3_strategy3.py markdown TypeError（已修）

**问题**：commit `d6ac629` 重构 `run_overseas_champion` 走状态化路径后，`_result_to_legacy_dict` 把 `revenue_yi` 设成 `None`。但 `scripts/run_phase3_strategy3.py:228` 用：

```python
f"{r['revenue_yi']:.1f}"
```

`None:.1f` 抛 `TypeError: unsupported format character`。任何用户跑 `run_phase3_strategy3.py` 都会崩。测试没抓到是因为只验 DataFrame 长度和列名，没跑 markdown 输出。

**修复**：
1. `OverseasMetrics` 加 `total_revenue_yi` 字段（`src/screening/schemas.py`）。
2. `_evaluate_one_to_result` 写入 `metrics.overseas.total_revenue_yi = float(revenue) / 1e8`。
3. `_result_to_legacy_dict` 从 metrics 拿，填 `revenue_yi`。
4. `run_phase3_strategy3.py` markdown 加 `_fmt()` helper，对所有数值字段做 `pd.notna` + try/except 防御。

**测试**：`test_overseas_champion.py::test_hit_returns_one_row` 加断言 `revenue_yi ≈ 100.0` 和 `overseas_revenue_yi ≈ 30.0`。

**v3 文档建议补充**：§5 加一项"P1.5-补全 legacy CSV 字段"，因为还有 `ocf_net_yi / net_profit_yi / total_liabilities_yi / total_assets_yi / eps_current / eps_forecast_y1/y2 / eps_y1_growth / eps_y2_growth` 8 个字段在状态化后丢失。当前 CSV 用 `[c for c in cols if c in result.columns]` 兼容不崩，但信息少了一半。

### 2.3 AkShare revenue_yoy / gross_margin 百分数单位（已修）

**问题**：commit `d6ac629` 之前，`src/strategies/consumer_reversal.py` 用启发式：

```python
if abs(revenue_yoy_f) > 1:
    revenue_yoy_f = revenue_yoy_f / 100.0
```

但 AkShare `stock_financial_abstract` 的"营业总收入增长率"实际**总是百分数**（如茅台 2024 年 `revenue_yoy=15.66` 表示 15.66%）。启发式在两个场景出错：
- 营收同比 0.5%（百分数 0.5）→ `abs(0.5) > 1` 为 False → 不除 → 当成 50% → **误判高增**。
- 营收同比 -0.5%（百分数 -0.5）→ 当小数 -50% → **误判下滑**。

同样，`gross_margin` 是百分数（91.96 = 91.96%），相减得 pp（百分点），但阈值 `gross_margin_yoy_min=-0.005` 是小数（-0.5pp）→ 单位不匹配，下降 0.03pp 会触发拒绝（应在允许范围内）。

测试用小数构造数据所以过，但生产用 AkShare 真实数据会错。

**修复**：改成确定的 `/100` 转换；`gross_margin` 也 `/100`，与 `gross_margin_yoy_min=-0.005` 单位对齐。测试 fixture 改百分数（与生产一致）。

**v3 文档建议补充**：§3.3 P1-1 行加一句"已修 AkShare 百分数单位转换"。否则"P1-1 已加 PB/营收/毛利率验证"是空中楼阁——单位错的话验证全错。

---

## 三、🟡 v3 文档可以补充的地方

### 3.1 P1.5 优先级建议调整：本地落库应排最前

v3 §5 列了 P1.5-1 到 P1.5-5，顺序是：
1. P1.5-1 被忽视证据链
2. P1.5-2 海外收入解析困难样本
3. P1.5-3 财务估值本地落库
4. P1.5-4 行业覆盖和参数化
5. P1.5-5 RAG 升级

**建议把 P1.5-3 提到 P1.5-1 之前**，理由：

- 当前每次跑策略都实时拉 AkShare。30 只股票 ×（PE 历史 + 财务摘要 + 可能多次重试）= 几分钟到十几分钟。
- 财报季 AkShare 数据会变（同一报告期财报季中后期会被修订），跑两次结果可能不同 → **破坏可复盘性**（这是 P0 状态化改造的核心目标）。
- 其他 P1.5 项都依赖稳定的本地数据才有意义——例如被忽视证据跑两次结果不一致就无法横向比较。

**建议动作**：在 §5 开头加一句明确"P1.5-3 影响最大，应最先做；其他 P1.5 项依赖本地数据稳定"。

### 3.2 §5.1 依赖关系没说清

v3 §5.1 列了 4 个 collector（概念板块 / 热点 / 新闻 / 相对收益）+ `neglect_evidence`。但 `neglect_evidence` 是**聚合字段**，需要前 4 个数据齐才能算。

**建议**：在 §5.1 加一段：

> 前 4 个 collector 可以分批 ship（各自独立填 metrics），`neglect_evidence` 是最后一步聚合，依赖前 4 个完成。建议先做"研报覆盖度 + 新闻数"两项最容易拿的，热点/概念/相对收益作为第二批。

### 3.3 §8 README 数字校准

v3 §8 自己提了"应该更新 DuckDB 表数和脚本数"，但没给准确数字：

- DuckDB 表数：实际 13 张（含 `screen_runs` / `candidate_scores`），v3 §8 说"应更新为包含 screen_runs / candidate_scores 后的表数"但没说具体值。
- 脚本数：实际 11 个 `.py`（`run_after_disclosure.py` / `baseline_diff.py` / `data_source_baseline.py` 是新增），v3 §8 说"已有新增脚本"但没说具体值。

**建议**：v3 §8 直接给出准确数字，避免下游 README 同步时还要再数一遍。

### 3.4 §5.2 select_best_record ratio 校验的位置

v3 §5.2 建议动作第 3 条："`select_best_record` 增加总营收 ratio 校验，优先选择合理海外收入占比的候选。"

但 `select_best_record` 在 `src/collectors/annual_report_parser.py` 里，**parser 阶段拿不到总营收**（总营收来自 AkShare/新浪，不是 PDF）。所以 ratio 校验只能在策略层做：

- `src/strategies/overseas_champion.py:_evaluate_one_to_result` 已经有 `revenue`（总营收）和 `overseas_revenue`。
- 当前只有 `overseas_ratio_max=0.95` 单阈值过滤。
- 可以改成：从 `candidates_json` 里把所有候选都过一遍 ratio 校验，选 ratio 最合理的，而不是直接信 parser 选的最大值。

**建议**：v3 §5.2 把"ratio 校验在策略层做"明确写出来，否则按字面意思在 parser 加 ratio 校验会卡住。

---

## 四、🟢 小问题（不阻塞但建议改）

### 4.1 v3 §3.3 P1-1 描述遗漏单位修复

如 §2.3 所述，v3 §3.3 行"P1-1 已加入 PB 5 年分位、营收同比、毛利率同比改善；数据缺失降级 watch"漏了"已修百分数单位转换"。建议补一句。

### 4.2 v3 缺一个 P1.5 整体完成定义

§5.1-5.5 每个有自己的验收标准，但 P1.5 整体完成后应该是什么样？建议在 §5 末尾加一节"P1.5 完成定义"，例如：

> 跑一次 30 只样本 `run_after_disclosure.py --period 2025A --from-disclosures --limit 30 --strategy all`：
> - 总耗时 < 5 分钟（依赖 P1.5-3 本地落库）
> - markdown 报告能解释每只股票的 hit/watch/rejected/data_missing 原因
> - 策略三 watch 清单的 `neglect_evidence` 字段非空（依赖 P1.5-1）
> - 12 份 parser 失败 PDF 至少修复一半（依赖 P1.5-2）
> - 关键数据从本地 DuckDB 读取，AkShare 仅作 fallback

### 4.3 v3 §7.2 兼容入口的描述可以更明确

v3 §7.2 说"旧脚本继续保留"，但没说"`run_phase3_strategy3.py` 已经通过 `run_overseas_champion` 内部走状态化路径，行为和 `run_after_disclosure.py --strategy overseas` 一致"。建议补一句，避免读者以为两个入口行为不同。

### 4.4 v3 §6.3 统一 CLI 的优先级可以提前

v3 §6.3 列了 P2-3 统一 CLI，但当前已有 11 个脚本，新用户很难知道该用哪个。如果 P1.5 完成后立刻做 CLI 统一，对复盘体验提升很大。建议把 P2-3 提到 P1.5 之后立刻做（不一定要等评分层 P2-1）。

---

## 五、复核总结

### 5.1 已修但 v3 没记录的（必须补）

| # | 问题 | 严重度 | 修复 commit |
|---|------|-------|-----------|
| 2.1 | overseas 重复 `_evaluate_one` 导致旧 CSV 入口不走状态化 | P0 | d6ac629 |
| 2.2 | run_phase3_strategy3.py markdown TypeError（d6ac629 引入的回归） | P0 | 待 commit |
| 2.3 | AkShare revenue_yoy/gross_margin 百分数单位 | P1 | d6ac629 |

### 5.2 v3 文档可以补充的（建议改）

| # | 建议 | 优先级 |
|---|------|-------|
| 3.1 | P1.5-3 本地落库排到 P1.5-1 之前 | 高 |
| 3.2 | §5.1 写明 `neglect_evidence` 是聚合字段，依赖前 4 个 collector | 中 |
| 3.3 | §8 README 数字给出准确值（13 表 / 11 脚本） | 中 |
| 3.4 | §5.2 ratio 校验明确在策略层做，不在 parser | 中 |
| 4.1 | §3.3 P1-1 补"已修百分数单位转换" | 高 |
| 4.2 | §5 末尾加 P1.5 整体完成定义 | 中 |
| 4.3 | §7.2 补"两入口行为一致"说明 | 低 |
| 4.4 | §6.3 统一 CLI 提到 P1.5 之后立刻做 | 低 |

### 5.3 总体观感

v3 方向正确，README 同步做得到位，比旧 v2（1069 行含执行计划）清爽得多。v5 主要补的是：
- **今天的修复细节没进 v3**（dup `_evaluate_one` + 单位 + markdown 回归）。
- **P1.5 优先级建议调整**（本地落库应最先做，否则可复盘性是空中楼阁）。
- **几处依赖关系和位置说明**（`neglect_evidence` 聚合 / ratio 校验在策略层 / 数字校准）。

不阻塞 v3 落地，但建议下次更新 v3 时一并吸收。
