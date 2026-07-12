"""P2-3 动态监控：对比两次 screen_run 的 candidate_scores，输出状态/指标变化。

可监控的内容（参见 IMPROVEMENTS P2-3）：
- 新披露定期报告 → 新进入 hit 的股票
- 新增研报覆盖 → reports_count_90d 变化
- 热点/新闻数量突变 → news_count_30d 变化
- 候选池估值分位变化 → pe_pct_5y 变化
- 策略三海外收入解析状态变化 → parse_warning / overseas_ratio 变化

用法：
    from src.screening.run_diff import diff_runs
    diff = diff_runs(store, "run_a", "run_b")
    print(diff.to_markdown())

设计约束：
- 不主动改状态，只输出可读 diff
- 新 hit / 跌出 hit / 状态变化都进入 diff.events 列表
- 关键指标变化（超过阈值）单独列出
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from ..storage import DuckDBStore


# 关键指标变化阈值（超过此值才进入 diff）
DEFAULT_METRIC_THRESHOLDS: dict[str, float] = {
    "valuation.pe_ttm": 5.0,             # PE 变化超过 5
    "valuation.pe_pct_5y": 10.0,         # PE 分位变化超过 10pp
    "overseas.overseas_ratio": 0.10,     # 海外占比变化超过 10pp
    "overseas.overseas_yoy": 0.30,       # 海外同比变化超过 30pp
    "catalyst.reports_count_90d": 3,     # 研报数变化超过 3 篇
    "catalyst.news_count_30d": 10,       # 新闻数变化超过 10 条
    "score.final_score": 0.10,           # 综合评分变化超过 0.1
}


@dataclass
class DiffEvent:
    """单只股票的某个变化事件。"""
    code: str
    name: Optional[str]
    strategy: str
    kind: str  # new_hit / dropped_hit / status_changed / metric_changed / new_parse_warning
    detail: str
    before: Optional[object] = None
    after: Optional[object] = None
    metric_key: Optional[str] = None  # 仅 metric_changed 使用

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "strategy": self.strategy,
            "kind": self.kind,
            "detail": self.detail,
            "before": self.before,
            "after": self.after,
            "metric_key": self.metric_key,
        }


@dataclass
class RunDiff:
    """两次 run 的对比结果。"""
    before_run_id: str
    after_run_id: str
    before_started_at: Optional[str] = None
    after_started_at: Optional[str] = None
    events: list[DiffEvent] = field(default_factory=list)

    @property
    def new_hits(self) -> list[DiffEvent]:
        return [e for e in self.events if e.kind == "new_hit"]

    @property
    def dropped_hits(self) -> list[DiffEvent]:
        return [e for e in self.events if e.kind == "dropped_hit"]

    @property
    def status_changes(self) -> list[DiffEvent]:
        return [e for e in self.events if e.kind == "status_changed"]

    @property
    def metric_changes(self) -> list[DiffEvent]:
        return [e for e in self.events if e.kind == "metric_changed"]

    @property
    def parse_warning_changes(self) -> list[DiffEvent]:
        return [e for e in self.events if e.kind == "new_parse_warning"]

    def to_markdown(self) -> str:
        """输出 Markdown 报告。"""
        lines = [
            f"# Run Diff",
            "",
            f"- before: `{self.before_run_id}` ({self.before_started_at})",
            f"- after: `{self.after_run_id}` ({self.after_started_at})",
            f"- total events: {len(self.events)}",
            f"  - new_hit: {len(self.new_hits)}",
            f"  - dropped_hit: {len(self.dropped_hits)}",
            f"  - status_changed: {len(self.status_changes)}",
            f"  - metric_changed: {len(self.metric_changes)}",
            f"  - new_parse_warning: {len(self.parse_warning_changes)}",
            "",
        ]

        def _table(events: list[DiffEvent], title: str) -> None:
            if not events:
                return
            lines.append(f"## {title}")
            lines.append("")
            lines.append("| 代码 | 名称 | 策略 | 详情 |")
            lines.append("|------|------|------|------|")
            for e in events:
                lines.append(
                    f"| {e.code} | {e.name or ''} | {e.strategy} | {e.detail} |"
                )
            lines.append("")

        _table(self.new_hits, "新进入 hit")
        _table(self.dropped_hits, "跌出 hit")
        _table(self.status_changes, "状态变化")
        _table(self.parse_warning_changes, "新增 parse_warning")
        if self.metric_changes:
            lines.append("## 关键指标变化")
            lines.append("")
            lines.append("| 代码 | 名称 | 策略 | 指标 | before | after |")
            lines.append("|------|------|------|------|--------|-------|")
            for e in self.metric_changes:
                lines.append(
                    f"| {e.code} | {e.name or ''} | {e.strategy} "
                    f"| {e.metric_key} | {e.before} | {e.after} |"
                )
            lines.append("")

        return "\n".join(lines)


def _load_run(store: DuckDBStore, run_id: str) -> pd.DataFrame:
    """加载某 run 的 candidate_scores。"""
    return store.load_candidate_scores(run_id)


def _get_run_started_at(store: DuckDBStore, run_id: str) -> Optional[str]:
    """拿 run 的 started_at 时间戳。"""
    df = store.load_screen_run(run_id)
    if df.empty:
        return None
    val = df.iloc[0].get("started_at")
    return str(val) if val is not None else None


def _parse_metrics(metrics_json: Optional[str]) -> dict:
    if not metrics_json:
        return {}
    try:
        return json.loads(metrics_json)
    except (ValueError, TypeError):
        return {}


def _get_nested(d: dict, dotted_key: str) -> Optional[object]:
    """取嵌套 dict 的值。例如 _get_nested(d, "valuation.pe_ttm")。"""
    parts = dotted_key.split(".")
    cur: object = d
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _diff_metrics(
    before: dict, after: dict, thresholds: dict[str, float]
) -> list[tuple[str, object, object]]:
    """对比两个 metrics dict，返回 (key, before, after) 列表（仅超过阈值的）。"""
    out: list[tuple[str, object, object]] = []
    for key, threshold in thresholds.items():
        b = _get_nested(before, key)
        a = _get_nested(after, key)
        if b is None and a is None:
            continue
        if b is None or a is None:
            # 从无到有 / 从有到无：记录
            if b != a:
                out.append((key, b, a))
            continue
        try:
            diff = abs(float(a) - float(b))
        except (ValueError, TypeError):
            continue
        if diff > threshold:
            out.append((key, b, a))
    return out


def diff_runs(
    store: DuckDBStore,
    before_run_id: str,
    after_run_id: str,
    *,
    thresholds: Optional[dict[str, float]] = None,
) -> RunDiff:
    """对比两次 run 的 candidate_scores。

    Args:
        store: DuckDBStore
        before_run_id: 旧 run（基线）
        after_run_id: 新 run
        thresholds: 关键指标变化阈值（None 时用默认）

    Returns:
        RunDiff，包含所有变化事件
    """
    thresholds = thresholds or DEFAULT_METRIC_THRESHOLDS
    before_started = _get_run_started_at(store, before_run_id)
    after_started = _get_run_started_at(store, after_run_id)
    diff = RunDiff(
        before_run_id=before_run_id,
        after_run_id=after_run_id,
        before_started_at=before_started,
        after_started_at=after_started,
    )

    df_b = _load_run(store, before_run_id)
    df_a = _load_run(store, after_run_id)
    if df_b.empty and df_a.empty:
        return diff

    # 用 (code, strategy) 做 join key
    def _key(row):
        return (str(row.get("code")).zfill(6), str(row.get("strategy")))

    before_map: dict[tuple[str, str], pd.Series] = {}
    for _, row in df_b.iterrows():
        before_map[_key(row)] = row
    after_map: dict[tuple[str, str], pd.Series] = {}
    for _, row in df_a.iterrows():
        after_map[_key(row)] = row

    all_keys = set(before_map.keys()) | set(after_map.keys())
    for key in all_keys:
        code, strategy = key
        b_row = before_map.get(key)
        a_row = after_map.get(key)
        name = (a_row.get("name") if a_row is not None else None) or (
            b_row.get("name") if b_row is not None else None
        )
        b_status = b_row.get("status") if b_row is not None else None
        a_status = a_row.get("status") if a_row is not None else None

        # 新增（之前不在 / 现在新增）
        if b_row is None and a_row is not None:
            if a_status == "hit":
                diff.events.append(DiffEvent(
                    code=code, name=name, strategy=strategy,
                    kind="new_hit",
                    detail=f"新增 hit（{a_row.get('hit_reason') or ''}）",
                ))
            else:
                diff.events.append(DiffEvent(
                    code=code, name=name, strategy=strategy,
                    kind="status_changed",
                    detail=f"新增（无前值）→ {a_status}",
                    before=None, after=a_status,
                ))
            continue

        # 消失（之前在 / 现在没了）
        if a_row is None and b_row is not None:
            if b_status == "hit":
                diff.events.append(DiffEvent(
                    code=code, name=name, strategy=strategy,
                    kind="dropped_hit",
                    detail=f"跌出 hit（之前 {b_status}）",
                ))
            else:
                diff.events.append(DiffEvent(
                    code=code, name=name, strategy=strategy,
                    kind="status_changed",
                    detail=f"{b_status} → 消失",
                    before=b_status, after=None,
                ))
            continue

        # 双方都在：检查状态变化
        if b_status != a_status:
            # 重点关注 hit ↔ 非 hit 的翻转
            if b_status == "hit" and a_status != "hit":
                diff.events.append(DiffEvent(
                    code=code, name=name, strategy=strategy,
                    kind="dropped_hit",
                    detail=f"hit → {a_status}（{a_row.get('reject_reason') or a_row.get('data_missing_reason') or a_row.get('watch_reason') or ''}）",
                    before=b_status, after=a_status,
                ))
            elif b_status != "hit" and a_status == "hit":
                diff.events.append(DiffEvent(
                    code=code, name=name, strategy=strategy,
                    kind="new_hit",
                    detail=f"{b_status} → hit（{a_row.get('hit_reason') or ''}）",
                    before=b_status, after=a_status,
                ))
            else:
                diff.events.append(DiffEvent(
                    code=code, name=name, strategy=strategy,
                    kind="status_changed",
                    detail=f"{b_status} → {a_status}",
                    before=b_status, after=a_status,
                ))

        # parse_warning 变化
        b_metrics = _parse_metrics(b_row.get("metrics_json"))
        a_metrics = _parse_metrics(a_row.get("metrics_json"))
        b_pw = _get_nested(b_metrics, "overseas.parse_warning")
        a_pw = _get_nested(a_metrics, "overseas.parse_warning")
        if not b_pw and a_pw:
            diff.events.append(DiffEvent(
                code=code, name=name, strategy=strategy,
                kind="new_parse_warning",
                detail=f"新增 parse_warning: {a_pw}",
                before=b_pw, after=a_pw,
            ))

        # 关键指标变化
        for mkey, b_val, a_val in _diff_metrics(b_metrics, a_metrics, thresholds):
            diff.events.append(DiffEvent(
                code=code, name=name, strategy=strategy,
                kind="metric_changed",
                detail=f"{mkey}: {b_val} → {a_val}",
                before=b_val, after=a_val,
                metric_key=mkey,
            ))

    return diff


def diff_latest_two_runs(
    store: DuckDBStore,
    *,
    strategy: Optional[str] = None,
    period: Optional[str] = None,
    thresholds: Optional[dict[str, float]] = None,
) -> Optional[RunDiff]:
    """便捷方法：取最近两次同 strategy+period 的 run 做 diff。

    Returns:
        RunDiff 或 None（不足两次 run 时）
    """
    runs = store.list_screen_runs(strategy=strategy, period=period, limit=2)
    if len(runs) < 2:
        return None
    # list_screen_runs 按 started_at DESC 排序：第一行是最新
    after_run_id = str(runs.iloc[0]["run_id"])
    before_run_id = str(runs.iloc[1]["run_id"])
    return diff_runs(
        store, before_run_id, after_run_id, thresholds=thresholds
    )


# P2-3 alert：从 RunDiff 中过滤高信号事件，写 alert 报告。
# 调度（cron/loop skill）由用户自行配置；本模块只负责"给定 diff，产 alert"。

# 默认 alert 触发阈值：metric_changed 事件超过此绝对值才进 alert
DEFAULT_ALERT_THRESHOLDS: dict[str, float] = {
    "valuation.pe_ttm": 10.0,            # PE 变化 > 10
    "valuation.pe_pct_5y": 20.0,         # PE 分位变化 > 20pp
    "overseas.overseas_ratio": 0.15,     # 海外占比变化 > 15pp
    "score.final_score": 0.15,           # 综合评分变化 > 0.15
}


def filter_alertable_events(
    diff: RunDiff,
    *,
    alert_thresholds: Optional[dict[str, float]] = None,
) -> list[DiffEvent]:
    """P2-3：从 diff 中过滤值得告警的高信号事件。

    规则：
    - 所有 new_hit / dropped_hit 都告警（hit 翻转本身就是大事件）
    - new_parse_warning 都告警（数据质量退化）
    - status_changed 仅 hit ↔ watch 翻转告警（hit→rejected 太常见）
    - metric_changed 仅超过 alert_thresholds 的告警（比 DEFAULT_METRIC_THRESHOLDS 更严）

    Args:
        diff: RunDiff 对象
        alert_thresholds: 指定哪些 metric 变化值得告警；None 用默认

    Returns:
        待告警的 DiffEvent 列表（按 kind 分组无排序保证）
    """
    thresholds = alert_thresholds or DEFAULT_ALERT_THRESHOLDS
    out: list[DiffEvent] = []

    for e in diff.events:
        if e.kind in ("new_hit", "dropped_hit", "new_parse_warning"):
            out.append(e)
        elif e.kind == "status_changed":
            # hit ↔ watch 翻转才告警
            before_str = str(e.before) if e.before is not None else ""
            after_str = str(e.after) if e.after is not None else ""
            if "hit" in (before_str + after_str) and "watch" in (
                before_str + after_str
            ):
                out.append(e)
        elif e.kind == "metric_changed":
            if e.metric_key not in thresholds:
                continue
            threshold = thresholds[e.metric_key]
            try:
                b = float(e.before) if e.before is not None else 0.0
                a = float(e.after) if e.after is not None else 0.0
                if abs(a - b) >= threshold:
                    out.append(e)
            except (ValueError, TypeError):
                continue
    return out


def write_alert_report(
    diff: RunDiff,
    *,
    output_path: Path | str | None = None,
    alert_thresholds: Optional[dict[str, float]] = None,
) -> str:
    """生成 alert markdown 报告（高信号事件子集）。

    Args:
        diff: RunDiff
        output_path: 写入文件路径；None 则返回字符串
        alert_thresholds: filter_alertable_events 的阈值

    Returns:
        markdown 字符串
    """
    events = filter_alertable_events(
        diff, alert_thresholds=alert_thresholds
    )
    new_hits = [e for e in events if e.kind == "new_hit"]
    dropped = [e for e in events if e.kind == "dropped_hit"]
    status_flips = [
        e for e in events
        if e.kind == "status_changed"
    ]
    warnings = [e for e in events if e.kind == "new_parse_warning"]
    metric_alerts = [
        e for e in events if e.kind == "metric_changed"
    ]

    lines = [
        "# Alert Report",
        "",
        f"- before: `{diff.before_run_id}` ({diff.before_started_at})",
        f"- after: `{diff.after_run_id}` ({diff.after_started_at})",
        f"- 待告警事件：{len(events)} 条（全部 diff 事件 {len(diff.events)} 条）",
        "",
    ]

    def _table(rows: list[DiffEvent], title: str, cols: list[str]) -> None:
        if not rows:
            return
        lines.append(f"## {title} ({len(rows)})")
        lines.append("")
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["------"] * len(cols)) + "|")
        for e in rows:
            cells = []
            for c in cols:
                if c == "代码":
                    cells.append(e.code)
                elif c == "名称":
                    cells.append(e.name or "")
                elif c == "策略":
                    cells.append(e.strategy)
                elif c == "详情":
                    cells.append(e.detail)
                elif c == "指标":
                    cells.append(e.metric_key or "")
                elif c == "before":
                    cells.append(str(e.before))
                elif c == "after":
                    cells.append(str(e.after))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    _table(new_hits, "🆕 新进入 hit", ["代码", "名称", "策略", "详情"])
    _table(dropped, "❌ 跌出 hit", ["代码", "名称", "策略", "详情"])
    _table(status_flips, "🔄 hit ↔ watch 翻转", ["代码", "名称", "策略", "详情"])
    _table(warnings, "⚠ 新增 parse_warning", ["代码", "名称", "策略", "详情"])
    _table(
        metric_alerts, "📊 关键指标大幅变化",
        ["代码", "名称", "策略", "指标", "before", "after"],
    )

    if not events:
        lines.append("_(无待告警事件)_")

    md = "\n".join(lines)
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
    return md
