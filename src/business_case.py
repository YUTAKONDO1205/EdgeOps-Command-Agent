"""
Deterministic business-impact / ROI estimator.

The LLM agents narrate; this module puts a *yen figure* on the decision so
the value story is quantified, not adjectival. It is intentionally
rule-based and auditable (no LLM call): every number traces back to the
shipped CSVs (``data/failure_history.csv`` for realised downtime,
``data/parts_inventory.csv`` for parts cost / lead time) plus a small set of
clearly-stated assumptions.

Headline model — *avoided cost of early intervention*:

    放置 (run-to-failure)  : 計画外停止 downtime_h × ライン停止コスト/h
                             + 緊急部品調達（割増） + 二次故障
    早期介入 (planned)     : 計画停止枠で実施 → 生産影響 ≈ 0、部品は通常価格
    回避コスト             = 放置コスト − 早期介入コスト
    期待回避コスト         = 回避コスト × 悪化確率(risk level 依存)

The single tunable assumption — line-stop cost per hour — is exposed via the
``EDGEOPS_LINE_STOP_COST_JPY_PER_HOUR`` env var so a customer can drop in
their own number. The default (¥300,000/h) is a deliberately conservative
mid-size-line figure; state it as an assumption in any customer-facing output.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from .utils import DATA_DIR


# ── Tunable assumptions ─────────────────────────────────────────────────────
DEFAULT_LINE_STOP_COST_PER_HOUR_JPY = 300_000
# Emergency procurement / expedite surcharge applied to parts when a failure
# is run to completion instead of caught early (express shipping, overtime).
EMERGENCY_PARTS_PREMIUM = 0.5
# Probability that a given risk level escalates to an unplanned stop if left
# alone — used to turn a worst-case figure into an *expected* avoided cost.
ESCALATION_PROBABILITY: dict[str, float] = {"Critical": 1.0, "Warning": 0.4, "Normal": 0.0}
# Fallback realised-downtime (hours) when an equipment has no Critical history.
DEFAULT_DOWNTIME_BY_RISK: dict[str, float] = {"Critical": 6.0, "Warning": 3.0, "Normal": 0.0}


def line_stop_cost_per_hour() -> int:
    raw = os.getenv("EDGEOPS_LINE_STOP_COST_JPY_PER_HOUR", "").strip()
    if raw:
        try:
            return max(0, int(float(raw)))
        except ValueError:
            pass
    return DEFAULT_LINE_STOP_COST_PER_HOUR_JPY


# ── Data access (defensive: missing/garbled CSVs degrade to defaults) ────────
def _read_csv(name: str) -> list[dict[str, str]]:
    path = DATA_DIR / name
    try:
        with path.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error):
        return []


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PartRisk:
    part_id: str
    name: str
    unit_price_jpy: int
    stock: int
    reorder_point: int
    lead_time_days: int

    @property
    def out_of_stock(self) -> bool:
        return self.stock <= 0

    @property
    def below_reorder(self) -> bool:
        return self.stock <= self.reorder_point

    @property
    def availability_note(self) -> str:
        if self.out_of_stock:
            return f"在庫0・調達 {self.lead_time_days} 日（即日対応不可）"
        if self.below_reorder:
            return f"在庫 {self.stock}・発注点割れ（リード {self.lead_time_days} 日）"
        return f"在庫 {self.stock}・即日対応可"


@dataclass(frozen=True)
class BusinessImpact:
    equipment_id: str
    risk_level: str
    line_stop_cost_per_hour: int
    expected_downtime_hours: float
    escalation_probability: float
    unplanned_stop_cost: int
    parts_cost: int
    emergency_premium_cost: int
    run_to_failure_cost: int
    planned_intervention_cost: int
    gross_avoided_cost: int
    expected_avoided_cost: int
    parts: list[PartRisk] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    historical_reference: str = ""

    @property
    def lead_time_risk(self) -> bool:
        return any(p.out_of_stock or p.below_reorder for p in self.parts)

    def headline_one_liner(self) -> str:
        y = lambda v: f"¥{v:,}"
        if self.risk_level == "Critical":
            return (
                f"放置で計画外停止 約{self.expected_downtime_hours:.0f}h・"
                f"約{y(self.run_to_failure_cost)} の損失リスク。"
                f"早期介入で約{y(self.expected_avoided_cost)} を回避可能。"
            )
        if self.risk_level == "Warning":
            return (
                f"悪化して計画外停止に至れば約{y(self.run_to_failure_cost)}。"
                f"早期確認による期待回避額 約{y(self.expected_avoided_cost)}"
                f"（悪化確率 {self.escalation_probability:.0%} 想定）。"
            )
        return "現時点で生産影響なし（想定回避額 ¥0）。次回定期点検でトレンド比較。"

    def to_markdown_block(self) -> str:
        y = lambda v: f"¥{v:,}"
        lines = [
            "## 9. ビジネスインパクト（自動算出・監査可能）",
            f"- **推奨判断の経済効果**: {self.headline_one_liner()}",
            "",
            "| 項目 | 金額 |",
            "|---|---|",
            f"| 放置時コスト（計画外停止 {self.expected_downtime_hours:.0f}h ＋ 緊急部品） | {y(self.run_to_failure_cost)} |",
            f"| 早期介入時コスト（計画停止・部品通常調達） | {y(self.planned_intervention_cost)} |",
            f"| 回避コスト（最悪ケース） | {y(self.gross_avoided_cost)} |",
            f"| **期待回避コスト（悪化確率 {self.escalation_probability:.0%} 反映）** | **{y(self.expected_avoided_cost)}** |",
        ]
        if self.parts:
            lines.append("")
            lines.append("**部品・調達リスク**")
            for p in self.parts:
                lines.append(f"- {p.name}（{p.part_id}, {y(p.unit_price_jpy)}）: {p.availability_note}")
        if self.historical_reference:
            lines.append("")
            lines.append(f"_過去実績参照: {self.historical_reference}_")
        if self.assumptions:
            lines.append("")
            lines.append("_前提: " + " / ".join(self.assumptions) + "_")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "equipment_id": self.equipment_id,
            "risk_level": self.risk_level,
            "line_stop_cost_per_hour": self.line_stop_cost_per_hour,
            "expected_downtime_hours": self.expected_downtime_hours,
            "escalation_probability": self.escalation_probability,
            "run_to_failure_cost": self.run_to_failure_cost,
            "planned_intervention_cost": self.planned_intervention_cost,
            "gross_avoided_cost": self.gross_avoided_cost,
            "expected_avoided_cost": self.expected_avoided_cost,
            "lead_time_risk": self.lead_time_risk,
            "parts": [
                {
                    "part_id": p.part_id,
                    "unit_price_jpy": p.unit_price_jpy,
                    "stock": p.stock,
                    "lead_time_days": p.lead_time_days,
                }
                for p in self.parts
            ],
            "historical_reference": self.historical_reference,
        }


def _critical_history(equipment_id: str) -> list[dict[str, str]]:
    rows = _read_csv("failure_history.csv")
    return [
        r for r in rows
        if r.get("equipment_id") == equipment_id and r.get("risk_level") == "Critical"
    ]


def _lookup_part(part_id: str) -> PartRisk | None:
    if not part_id or part_id.lower() in {"none", "—", ""}:
        return None
    for r in _read_csv("parts_inventory.csv"):
        if r.get("part_id") == part_id:
            return PartRisk(
                part_id=part_id,
                name=r.get("name", part_id),
                unit_price_jpy=_to_int(r.get("unit_price_jpy")),
                stock=_to_int(r.get("stock")),
                reorder_point=_to_int(r.get("reorder_point")),
                lead_time_days=_to_int(r.get("lead_time_days")),
            )
    return None


def estimate(equipment_id: str, risk_level: str) -> BusinessImpact:
    """Quantify the business impact of acting now vs running to failure.

    Pure function of the shipped CSVs + assumptions; safe to call in mock mode
    and deterministic so it can be asserted in tests and pasted into reports.
    """
    cost_per_h = line_stop_cost_per_hour()
    prob = ESCALATION_PROBABILITY.get(risk_level, 0.0)

    # Realised downtime + the part typically replaced, from this equipment's
    # own Critical history when available.
    history = _critical_history(equipment_id)
    parts: list[PartRisk] = []
    historical_reference = ""
    if history:
        downtime = mean(_to_float(r.get("downtime_hours")) for r in history) or \
            DEFAULT_DOWNTIME_BY_RISK.get("Critical", 6.0)
        latest = history[-1]
        historical_reference = (
            f"{latest.get('date', '')} {equipment_id} Critical: "
            f"{_to_float(latest.get('downtime_hours')):.0f}h停止 / "
            f"{latest.get('root_cause', '')}"
        )
        part = _lookup_part(latest.get("parts_replaced", ""))
        if part is not None:
            parts.append(part)
    else:
        downtime = DEFAULT_DOWNTIME_BY_RISK.get(risk_level, 0.0)

    # Normal: no avoided cost, but still report the (zero) figures cleanly.
    if risk_level == "Normal":
        downtime = 0.0

    parts_cost = sum(p.unit_price_jpy for p in parts)
    unplanned_stop_cost = int(round(downtime * cost_per_h))
    emergency_premium_cost = int(round(parts_cost * EMERGENCY_PARTS_PREMIUM))
    # Run-to-failure: unplanned stop + parts at emergency (expedited) price.
    run_to_failure_cost = unplanned_stop_cost + parts_cost + emergency_premium_cost
    # Planned intervention: done inside a scheduled downtime window so the
    # production loss is ~0; only the parts at normal price.
    planned_intervention_cost = parts_cost
    gross_avoided_cost = max(0, run_to_failure_cost - planned_intervention_cost)
    expected_avoided_cost = int(round(gross_avoided_cost * prob))

    assumptions = [
        f"ライン停止コスト ¥{cost_per_h:,}/h（EDGEOPS_LINE_STOP_COST_JPY_PER_HOUR で調整可）",
        f"緊急部品調達割増 {EMERGENCY_PARTS_PREMIUM:.0%}",
        "早期介入は計画停止枠で実施（生産影響≒0）と仮定",
    ]

    return BusinessImpact(
        equipment_id=equipment_id,
        risk_level=risk_level,
        line_stop_cost_per_hour=cost_per_h,
        expected_downtime_hours=downtime,
        escalation_probability=prob,
        unplanned_stop_cost=unplanned_stop_cost,
        parts_cost=parts_cost,
        emergency_premium_cost=emergency_premium_cost,
        run_to_failure_cost=run_to_failure_cost,
        planned_intervention_cost=planned_intervention_cost,
        gross_avoided_cost=gross_avoided_cost,
        expected_avoided_cost=expected_avoided_cost,
        parts=parts,
        assumptions=assumptions,
        historical_reference=historical_reference,
    )
