"""
Renders the human-facing artifacts:
- Work Order (作業指示書)
- Management Report (管理者向け1ページ報告書)

Both are produced as plain Markdown so they can be displayed in
Streamlit, exported to PDF, or pasted into a Teams message.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from . import prompts
from . import business_case
from .agents import AgentResult, LLMClient, PipelineResult
from .utils import safe_get, use_mock_mode, extract_json


JST = timezone(timedelta(hours=9), name="JST")


def _now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def render_work_order(result: PipelineResult, equipment_id: str = "Pump-03") -> str:
    plan = result.action_plan.output if isinstance(result.action_plan.output, dict) else {}
    risk = result.risk
    root_cause = result.root_cause.output if isinstance(result.root_cause.output, dict) else {}

    deadline = safe_get(plan, "deadline_hours", default=24)
    priority = safe_get(plan, "priority", default=risk.risk_level)
    steps = safe_get(plan, "work_steps", default=[]) or []
    tools = safe_get(plan, "required_tools", default=[]) or []
    parts = safe_get(plan, "required_parts", default=[]) or []
    safety = safe_get(plan, "safety_notes", default=[]) or []
    recording = safe_get(plan, "post_work_recording", default=[]) or []
    approval = safe_get(plan, "manager_approval_required", default=True)

    causes = safe_get(root_cause, "root_cause_hypotheses", default=[]) or []
    cause_lines = []
    for c in causes[:3]:
        if not isinstance(c, dict):
            continue
        cause_lines.append(f"- {c.get('cause', '')}（尤度: {c.get('likelihood', '')}）")
    causes_block = "\n".join(cause_lines) if cause_lines else "- （特になし）"

    lines = [
        f"# 作業指示書 / Work Order",
        "",
        f"- **対象設備**: {equipment_id}",
        f"- **発行日時**: {_now_jst()}",
        f"- **リスクレベル**: {risk.risk_level}",
        f"- **優先度**: {priority}",
        f"- **対応期限**: {deadline} 時間以内",
        f"- **管理者承認**: {'必要' if approval else '不要'}",
        "",
        "## 想定原因（仮説）",
        causes_block,
        "",
        "## 作業手順",
    ]
    if steps:
        for s in steps:
            lines.append(f"{s}" if str(s).strip().startswith(tuple(f"{i}." for i in range(10))) else f"- {s}")
    else:
        lines.append("- （手順なし）")

    lines += ["", "## 必要工具"]
    lines += [f"- {t}" for t in tools] if tools else ["- （特になし）"]

    lines += ["", "## 必要部品"]
    lines += [f"- {p}" for p in parts] if parts else ["- （特になし）"]

    lines += ["", "## 安全上の注意"]
    lines += [f"- {s}" for s in safety] if safety else ["- （特になし）"]

    lines += ["", "## 作業後の記録項目"]
    lines += [f"- {r}" for r in recording] if recording else ["- （特になし）"]

    lines += [
        "",
        "---",
        "本作業指示書は AI による提案です。設備停止や部品交換などの判断は、必ず管理者が確認してください。",
    ]
    return "\n".join(lines)


def render_management_report(result: PipelineResult, equipment_id: str = "Pump-03", *, client: LLMClient | None = None) -> str:
    """Generate a 1-page management report.

    Tries the Report Agent (LLM) first. Falls back to a deterministic
    template when LLM is unavailable. Either way, the output is plain
    Markdown that the UI can show or export.
    """
    combined = result.as_combined_summary()
    # Deterministic ROI figures — always computed in-process (never from the
    # LLM) so the yen numbers in the report are auditable, not hallucinated.
    impact = business_case.estimate(equipment_id, result.risk.risk_level)

    body: str | None = None
    if not use_mock_mode():
        try:
            client = client or LLMClient()
            raw = client.complete(
                system=prompts.SYSTEM_BASE,
                user=prompts.REPORT_AGENT_PROMPT.format(
                    combined_summary=json.dumps(combined, ensure_ascii=False, indent=2),
                ),
                json_mode=False,
            )
            if raw and len(raw.strip()) > 100:
                body = raw
        except Exception:
            body = None

    if body is None:
        body = _fallback_report(result, equipment_id)

    body = body.strip() + "\n\n" + impact.to_markdown_block()
    return _decorate_report(body, equipment_id, result.risk.risk_level, impact)


def _decorate_report(
    body: str,
    equipment_id: str,
    risk_level: str,
    impact: "business_case.BusinessImpact | None" = None,
) -> str:
    verdict = f"> **推奨判断**: {impact.headline_one_liner()}\n\n" if impact is not None else ""
    header = (
        f"# 設備保全 管理者報告書\n\n"
        f"- **対象設備**: {equipment_id}\n"
        f"- **発行日時**: {_now_jst()}\n"
        f"- **リスクレベル**: {risk_level}\n\n"
        f"---\n\n"
        f"{verdict}"
    )
    footer = (
        "\n\n---\n\n"
        "本報告書は AI による提案です。最終的な対応方針の決定は管理者が行ってください。"
    )
    return header + body.strip() + footer


def _fallback_report(result: PipelineResult, equipment_id: str) -> str:
    risk = result.risk
    signal_summary = safe_get(result.signal.output if isinstance(result.signal.output, dict) else {}, "summary", default="")
    rc = result.root_cause.output if isinstance(result.root_cause.output, dict) else {}
    rc_summary = safe_get(rc, "abnormality_summary", default="現状の異常傾向の要約は得られませんでした。")
    causes = safe_get(rc, "root_cause_hypotheses", default=[]) or []
    plan = result.action_plan.output if isinstance(result.action_plan.output, dict) else {}
    deadline = safe_get(plan, "deadline_hours", default=24)
    steps = safe_get(plan, "work_steps", default=[]) or []
    safety = safe_get(plan, "safety_notes", default=[]) or []

    cause_lines = []
    for c in causes[:3]:
        if not isinstance(c, dict):
            continue
        cause_lines.append(f"- {c.get('cause', '')}（尤度 {c.get('likelihood', '?')}）: {c.get('reason', '')}")

    risk_narrative = {
        "Normal": "現時点で運転継続に支障はありません。次回定期点検時にトレンドを再確認します。",
        "Warning": "悪化した場合、計画外の停止リスクが発生します。早期の追加確認で計画保全に収められます。",
        "Critical": "放置すると設備停止または二次損傷の可能性があります。早期介入を強く推奨します。",
    }.get(risk.risk_level, "")

    next_inspection = {
        "Normal": "次回の定期点検時に通常点検を実施",
        "Warning": "72時間以内に追加点検を実施、その後トレンド監視を強化",
        "Critical": "24時間以内に詳細点検を実施、結果に応じて部品手配と停止判断",
    }.get(risk.risk_level, "次回点検計画を再評価")

    parts = [
        "## 1. エグゼクティブサマリー",
        f"{equipment_id} について、{signal_summary or rc_summary}",
        "",
        "## 2. 異常内容",
        *(f"- {e}" for e in (risk.evidence_lines() or ["現時点で異常は検出されていません"])),
        "",
        "## 3. 判断根拠",
        f"- ヘルススコア: {risk.health_score} / 100",
        f"- 主要懸念: {risk.primary_concern}",
        f"- ルールベース判定: {risk.risk_level}",
        "",
        "## 4. 推定原因",
        *(cause_lines or ["- 明確な原因は特定されていません"]),
        "",
        "## 5. 推奨対応と期限",
        f"- 対応期限: {deadline} 時間以内",
        *([f"- {s}" for s in steps[:5]] or ["- 通常運転を継続"]),
        "",
        "## 6. 放置した場合のリスク",
        f"- {risk_narrative}",
        "",
        "## 7. 人間確認ポイント",
        *(f"- {s}" for s in (safety or ["管理者による最終承認が必要"])),
        "",
        "## 8. 次回点検計画",
        f"- {next_inspection}",
    ]
    return "\n".join(parts)
