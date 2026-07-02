# 策略二 Ground Truth 标注规则

策略二拆成两个子策略：

- `vbp_recovery`：集采冲击后修复型，核心验证量价齐升、毛利率/现金流修复和修复持续性。
- `innovation_export`：创新药/创新器械出海型，核心验证 License-out、海外临床/注册/商业化催化和 A/H/港股估值反应。

## 字段

CSV 模板位置：`data/exports/pharma_vbp_ground_truth.csv`。

必填字段：

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

## 修复起点季度

`recovery_start_quarter` 按优先级判断：

1. 收入同比由负转正，且毛利率同比下降幅度收窄。
2. 扣非净利润同比连续两期改善。
3. 经营现金流 / 净利润改善到 0.6 以上。
4. 财报或公告披露集采产品收入、销量或中标放量的具体数字。

若只有“集采影响逐步出清”等话术，不允许标为明确修复起点，只能标 `watch`。

## 人工标签

- `hit`：财务改善连续至少 2 个季度，且有数字证据支持集采出清、产品结构改善或创新/出海催化。
- `watch`：财务改善刚出现，但证据不足或存在明显风险。
- `false_positive`：收入或利润改善来自一次性因素、低质量应收、非经常性收益或模板话术。
- `false_negative`：规则未命中但人工确认存在真实修复或催化。

每只样本必须保留 `label_reason`，规则版本写入 `label_version`。
