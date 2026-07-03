# 策略二 Ground Truth 标注规则

策略二拆成两个子策略：

- `vbp_recovery`：集采冲击后修复型，核心验证量价齐升、毛利率/现金流修复和修复持续性。
- `innovation_export`：创新药/创新器械出海型，核心验证 License-out、海外临床/注册/商业化催化和 A/H/港股估值反应。

本文档先服务策略二A `vbp_recovery` 的 P0 数据闭环。所有样本必须能被追溯到财报、公告、集采中选/落选文件、研报或人工复核记录；没有数字证据时只能标 `watch`，不能标 `hit`。

## 1. 文件位置

CSV 模板位置：`data/exports/pharma_vbp_ground_truth.csv`。

集采事件模板位置：`data/exports/pharma_vbp_events.csv`。

初始化模板：

```bash
python3 -m src.pipeline.cli pharma-template
```

校验：

```bash
python3 -m src.pipeline.cli pharma-vbp --csv data/exports/pharma_vbp_events.csv
python3 -m src.pipeline.cli pharma-gt --csv data/exports/pharma_vbp_ground_truth.csv
```

## 2. Ground Truth 字段

- `code`
- `name`
- `sub_strategy`
- `sub_industry`
- `vbp_batch`
- `vbp_status`
- `shock_start_quarter`
- `recovery_start_quarter`
- `recovery_quarter_count`
- `recovery_basis`
- `price_performance_window`
- `relative_return`
- `human_label`
- `label_reason`
- `label_version`

字段口径：

- `sub_strategy`：`vbp_recovery` / `innovation_export`。
- `vbp_status`：`won` / `lost` / `not_applicable` / `unknown`。
- `shock_start_quarter`：集采冲击开始季度，如 `2022Q4`。
- `recovery_start_quarter`：首次出现可验证修复的季度，如 `2024Q2`。
- `recovery_quarter_count`：从修复起点开始连续满足修复条件的季度数。
- `recovery_basis`：修复依据，必须写具体数字或证据摘要。
- `price_performance_window`：股价验证窗口，如 `20d` / `60d` / `120d` / `next_report`。
- `relative_return`：相对基准收益，小数口径，如 `0.12` 表示 12%。
- `label_version`：规则版本，当前写 `v1`。

## 3. 集采事件字段

`pharma_vbp_events.csv` 必填：

- `code`
- `name`
- `product_name`
- `vbp_batch`
- `vbp_status`
- `source`
- `source_url`
- `evidence_text`

推荐补充：

- `tender_date`
- `province`
- `price_before`
- `price_after`
- `volume_commitment`

要求：

- `source_url` 必须能追溯到联采办、地方阳光采购平台、公司公告、交易所互动、财报或可信研报。
- `evidence_text` 必须包含中选/落选/续约/价格/采购量等关键文字，不接受“见公告”等空泛描述。
- 若 `price_before` 和 `price_after` 同时填写，`price_after` 不应高于 `price_before`；否则先复核数据源。

## 4. 修复起点季度

`recovery_start_quarter` 按优先级判断：

1. 收入同比由负转正，且毛利率同比下降幅度收窄。
2. 扣非净利润同比连续两期改善。
3. 经营现金流 / 净利润改善到 0.6 以上。
4. 财报或公告披露集采产品收入、销量或中标放量的具体数字。

若只有“集采影响逐步出清”等话术，不允许标为明确修复起点，只能标 `watch`。

## 5. 修复持续季度数

从 `recovery_start_quarter` 开始，连续满足以下至少两项的季度数：

- 收入同比为正。
- 扣非净利润同比改善。
- 毛利率同比降幅收窄或环比改善。
- 经营现金流不恶化。
- 应收账款增速不显著高于收入增速。

若中途出现两项以上恶化，则修复连续性中断。

## 6. 人工标签

- `hit`：财务改善连续至少 2 个季度，且有数字证据支持集采出清、产品结构改善或创新/出海催化。
- `watch`：财务改善刚出现，但证据不足或存在明显风险。
- `false_positive`：收入或利润改善来自一次性因素、低质量应收、非经常性收益或模板话术。
- `false_negative`：规则未命中但人工确认存在真实修复或催化。

每只样本必须保留 `label_reason`，规则版本写入 `label_version`。

## 7. 常见反例

不能标 `hit` 的情形：

- 收入改善来自并表、会计口径变化或一次性大单，核心产品未放量。
- 净利润改善主要来自投资收益、公允价值变动或政府补助。
- 毛利率继续明显下行，收入增长只是低价放量。
- 应收账款增速显著高于收入增速，现金流没有跟上。
- 只有研报话术，没有财报、公告或集采文件的数字证据。

## 8. 策略二A MVP 对应关系

`python3 -m src.pipeline.cli pharma-screen --period <period>` 的 MVP 只使用本地 DuckDB：

- 候选池：`stock_industry` 中申万一级为 `医药生物` 且能归类为 `vbp_recovery`。
- 集采事件：`pharma_vbp_events` 中同股票的可追溯事件。
- 财务验证：`financials` 中目标报告期的收入同比、净利润同比、毛利率同比变化、经营现金流、扣非净利润。

MVP 的 `hit` 只是“规则确认的候选”，不是最终投资结论；最终仍需人工标签和后续回测/财务验证校准。
