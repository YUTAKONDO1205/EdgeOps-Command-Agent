"""
Multi-Agent layer.

Each agent has a single responsibility and returns a structured dict.
The orchestration logic in `run_pipeline` mirrors the demo narrative:

    Signal → Vision → Manual RAG → Root Cause → Action Planning → Report

If Azure OpenAI is configured (.env), agents call the real model.
Otherwise they fall back to deterministic mocks so the entire demo
works offline — important for hackathon judging when the network is
flaky.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import prompts
from .rag import RetrievalResult, build_query_from_findings, search as manual_search
from .risk_engine import RiskAssessment
from .signal_analysis import SignalFeatures
from .utils import extract_json, load_env, safe_get, use_mock_mode


# ──────────────────────────────────────────────────────────────────────────
# LLM client
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    endpoint: str | None
    api_key: str | None
    deployment: str | None
    vision_deployment: str | None
    api_version: str

    @classmethod
    def from_env(cls) -> "LLMConfig":
        load_env()
        return cls(
            endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            vision_deployment=os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT") or os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        )

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.deployment)


class LLMClient:
    """Thin wrapper that prefers Semantic Kernel for text and falls back to
    the raw `AzureOpenAI` SDK.

    Routing
    -------
    * `complete()` (text) → Semantic Kernel `Kernel` with `AzureChatCompletion`
      service registered. The kernel is built and cached in
      `src.sk_orchestrator`. If SK raises, we fall back to the raw SDK so
      a single SK incompatibility never breaks the demo.
    * `complete_with_image()` (vision) → raw `AzureOpenAI` SDK, because
      SK's multimodal content surface adds friction without changing
      the evaluation story.

    `last_text_source` records which path the most recent text call took
    ("semantic_kernel" / "azure_openai_sdk"), so the UI can surface it.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self._client = None
        self.last_text_source: str | None = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from openai import AzureOpenAI  # imported lazily
        self._client = AzureOpenAI(
            azure_endpoint=self.config.endpoint,
            api_key=self.config.api_key,
            api_version=self.config.api_version,
        )
        return self._client

    def complete(self, system: str, user: str, *, temperature: float = 0.2, json_mode: bool = True) -> str:
        # Prefer Semantic Kernel — that's the orchestration story we tell.
        try:
            from . import sk_orchestrator
            if sk_orchestrator.is_available():
                out = sk_orchestrator.complete(system, user, temperature=temperature, json_mode=json_mode)
                self.last_text_source = "semantic_kernel"
                return out
        except Exception:
            # Any SK-side failure (import/runtime) — keep the demo alive.
            pass

        # Fallback: raw Azure OpenAI SDK.
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.config.deployment,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        self.last_text_source = "azure_openai_sdk"
        return resp.choices[0].message.content or ""

    def complete_with_image(self, system: str, user_text: str, image_path: Path, *, temperature: float = 0.2) -> str:
        return self.complete_with_images(system, user_text, [(image_path, None)], temperature=temperature)

    def complete_with_images(
        self,
        system: str,
        user_text: str,
        images: list[tuple[Path, str | None]],
        *,
        temperature: float = 0.2,
        max_image_side: int = 1920,
        detail: str = "high",
    ) -> str:
        """Send N images in a single multimodal request.

        Each tuple is ``(path, caption)`` — captions get inlined as text
        before the image so the model can disambiguate "overview vs detail
        vs reference normal."

        Images are resized to ``max_image_side`` on the longest edge before
        base64-encoding so the prompt token budget stays predictable for
        smartphone-quality photos that can otherwise be 8 MP+.
        """
        client = self._ensure_client()
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for idx, (path, caption) in enumerate(images, start=1):
            data_url = _encode_image(Path(path), max_side=max_image_side)
            tag = caption.strip() if caption else f"IMAGE {idx}"
            content.append({"type": "text", "text": f"--- {tag} ---"})
            content.append({"type": "image_url",
                            "image_url": {"url": data_url, "detail": detail}})
        resp = client.chat.completions.create(
            model=self.config.vision_deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""


def _encode_image(path: Path, *, max_side: int = 1920) -> str:
    """Resize-aware base64 encoder. Falls back to raw bytes if PIL isn't
    available (the openai SDK still accepts oversized images, just less
    economically)."""
    try:
        from PIL import Image
        import io
        with Image.open(path) as img:
            img = img.convert("RGB")
            longest = max(img.size)
            if longest > max_side:
                ratio = max_side / longest
                img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)),
                                 Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85, optimize=True)
            data = buf.getvalue()
    except Exception:
        data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


# ──────────────────────────────────────────────────────────────────────────
# Agent result type
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    name: str
    output: dict[str, Any] | str
    source: str          # "azure_openai" | "mock"
    elapsed_ms: int = 0
    error: str | None = None


_HEAVY_KEYS = {"evidence_images", "overlay", "enhanced", "crops", "thumb", "thumbnail"}


def _strip_for_llm(value: Any) -> Any:
    """Return a deep copy of ``value`` with binary/data-URL payloads removed.

    Vision agent attaches base64 image data URLs (overlay / enhanced / crops)
    to its output so the UI can render annotated evidence. Those payloads must
    NOT be re-serialized into prompts for downstream agents — they explode the
    token count (one image ≈ 100k+ tokens) and trigger context_length_exceeded.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _HEAVY_KEYS:
                continue
            if isinstance(v, str) and v.startswith("data:image/"):
                continue
            out[k] = _strip_for_llm(v)
        return out
    if isinstance(value, list):
        return [_strip_for_llm(v) for v in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return ""
    return value


# ──────────────────────────────────────────────────────────────────────────
# Intake Agent — pipeline entry. Validates the input bundle and lets every
# downstream agent know what's reliable vs missing vs degraded.
# ──────────────────────────────────────────────────────────────────────────

def run_intake_agent(
    features: SignalFeatures,
    risk: RiskAssessment,
    *,
    equipment_id: str | None,
    image_path: Path | None,
    extra_image_paths: list[Path] | None,
    reference_image_path: Path | None,
    inspection_memo: str,
    client: LLMClient | None = None,
) -> AgentResult:
    """First-mile validation. Builds an input inventory so the Governance
    Agent at the end can audit "what data was actually available." Cheap by
    design — we don't want to add latency at the top of the pipeline."""
    available: list[str] = ["sensor_csv"]  # always present at this point
    missing: list[str] = []
    if image_path and Path(image_path).exists():
        available.append("primary_image")
    else:
        missing.append("primary_image")
    if extra_image_paths:
        available.append("extra_images")
    if reference_image_path and Path(reference_image_path).exists():
        available.append("reference_image")
    else:
        missing.append("reference_image")
    if inspection_memo and inspection_memo.strip():
        available.append("inspection_memo")
    else:
        missing.append("inspection_memo")

    summary = {
        "equipment_id": equipment_id or "Pump-03",
        "sample_count": features.sample_count,
        "duration_seconds": round(features.duration_seconds, 2),
        "primary_concern": risk.primary_concern,
        "risk_level": risk.risk_level,
        "available_sources": available,
        "missing_sources": missing,
    }
    if use_mock_mode():
        return AgentResult(
            name="Intake Agent", source="mock",
            output=_mock_intake(summary),
        )
    try:
        client = client or LLMClient()
        user = prompts.INTAKE_AGENT_PROMPT.format(
            intake_summary=json.dumps(summary, ensure_ascii=False, indent=2),
        )
        raw = client.complete(prompts.SYSTEM_BASE, user)
        parsed = extract_json(raw) or _mock_intake(summary)
        if not isinstance(parsed, dict):
            parsed = _mock_intake(summary)
        # Make sure the deterministic facts (sample count etc.) survive even
        # if the LLM tried to invent different numbers.
        parsed["sample_count"] = summary["sample_count"]
        parsed["duration_seconds"] = summary["duration_seconds"]
        parsed.setdefault("equipment_id", summary["equipment_id"])
        return AgentResult(name="Intake Agent", source="azure_openai", output=parsed)
    except Exception as exc:
        return AgentResult(name="Intake Agent", source="mock",
                           output=_mock_intake(summary), error=str(exc))


def _mock_intake(summary: dict[str, Any]) -> dict[str, Any]:
    anomalies: list[str] = []
    warnings: list[str] = []
    if summary["duration_seconds"] < 1.0:
        anomalies.append(
            f"観測時間が {summary['duration_seconds']}s と短く、長周期の傾向解析は困難")
    if summary["sample_count"] < 512:
        warnings.append("サンプル点数が少なめ。FFT 分解能が低くなる可能性")
    if "primary_image" not in summary["available_sources"]:
        warnings.append("点検写真なし。Vision Agent は所見を出さずに人間確認に回します")
    if "reference_image" not in summary["available_sources"]:
        warnings.append("参照（正常時）写真なし。comparison_to_normal は空欄になります")
    quality = "good"
    if anomalies:
        quality = "degraded"
    elif warnings:
        quality = "acceptable"
    return {
        "equipment_id": summary["equipment_id"],
        "data_quality": quality,
        "available_sources": summary["available_sources"],
        "missing_sources": summary["missing_sources"],
        "duration_seconds": summary["duration_seconds"],
        "sample_count": summary["sample_count"],
        "anomalies_in_input": anomalies or ["特になし"],
        "downstream_warnings": warnings or ["特になし"],
    }


# ──────────────────────────────────────────────────────────────────────────
# Signal Insight Agent
# ──────────────────────────────────────────────────────────────────────────

def run_signal_agent(features: SignalFeatures, risk: RiskAssessment, *, client: LLMClient | None = None) -> AgentResult:
    payload = {
        "features": features.to_dict(),
        "risk": risk.to_dict(),
    }
    user = prompts.SIGNAL_AGENT_PROMPT.format(
        features_json=json.dumps(features.to_dict(), ensure_ascii=False, indent=2),
        risk_json=json.dumps(risk.to_dict(), ensure_ascii=False, indent=2),
    )
    if use_mock_mode():
        return AgentResult(name="Signal Insight Agent", source="mock", output=_mock_signal(features, risk))
    try:
        client = client or LLMClient()
        raw = client.complete(prompts.SYSTEM_BASE, user)
        parsed = extract_json(raw) or _mock_signal(features, risk)
        return AgentResult(name="Signal Insight Agent", source="azure_openai", output=parsed if isinstance(parsed, dict) else {"summary": str(parsed)})
    except Exception as exc:
        return AgentResult(name="Signal Insight Agent", source="mock", output=_mock_signal(features, risk), error=str(exc))


def _mock_signal(features: SignalFeatures, risk: RiskAssessment) -> dict[str, Any]:
    observations: list[str] = []
    for f in risk.findings:
        if f.level != "Normal":
            observations.append(f.note)
    freq_findings: list[str] = []
    for peak in features.fft_peaks[:3]:
        if peak.frequency_hz < 1:
            continue
        if 100 <= peak.frequency_hz <= 300:
            freq_findings.append(
                f"{peak.frequency_hz:.0f}Hz 付近にピーク（振幅 {peak.amplitude:.4f}）。"
                "軸受異常帯域に該当する可能性"
            )
        elif 45 <= peak.frequency_hz <= 55:
            freq_findings.append(
                f"{peak.frequency_hz:.0f}Hz 付近のピークは主回転成分（正常範囲）"
            )
        else:
            freq_findings.append(f"{peak.frequency_hz:.0f}Hz 付近にピーク（振幅 {peak.amplitude:.4f}）")
    uncertainty: list[str] = []
    if risk.ambiguity_flag:
        uncertainty.append("一部の指標は警告レベルですが、確定診断には継続観察が必要です")
    if risk.risk_level == "Normal":
        uncertainty.append("断続的な軽微なゆらぎが将来悪化する可能性は排除できません")

    summary_map = {
        "Normal": f"主要指標はいずれも正常範囲内です（ヘルススコア {risk.health_score}）。",
        "Warning": f"複数の指標に軽度の上昇傾向が見られます（{risk.primary_concern}）。経過観察を推奨します。",
        "Critical": f"{risk.primary_concern}を含む複数指標が危険閾値を超過しています。早期点検が必要です。",
    }

    return {
        "summary": summary_map.get(risk.risk_level, "信号解析結果"),
        "key_observations": observations or ["特記事項なし"],
        "frequency_findings": freq_findings or ["顕著な異常周波数ピークは検出されません"],
        "uncertainty_notes": uncertainty or ["特になし"],
    }


# ──────────────────────────────────────────────────────────────────────────
# Vision Inspection Agent
# ──────────────────────────────────────────────────────────────────────────

def run_vision_agent(
    image_path: Path | None,
    inspection_memo: str,
    risk: RiskAssessment,
    *,
    client: LLMClient | None = None,
    equipment_id: str | None = None,
    extra_image_paths: list[Path] | None = None,
    reference_image_path: Path | None = None,
    features: "SignalFeatures | None" = None,
) -> AgentResult:
    """Run the Vision Inspection Agent.

    Parameters beyond the legacy ``image_path`` are all optional:

    - ``equipment_id`` enables the per-kind inspection checklist + region
      vocabulary. Without it the prompt still works but is generic.
    - ``extra_image_paths`` adds detail / multi-angle shots — they're tagged
      ``IMAGE 2..N`` so the model can refer to them.
    - ``reference_image_path`` (the "normal-state" photo) drives the
      ``comparison_to_normal`` field of the new schema.
    - ``features`` (SignalFeatures) lets us build the signal-correlation
      hint so the model cross-checks its visual observations against
      vibration / temperature / sound.
    """
    # Equipment-specific prompt augmentations (no-op if equipment_id None)
    kind, checklist_block, region_block = _vision_prompt_context(equipment_id)
    signal_hint = _signal_correlation_hint(features, risk)

    system_prompt = prompts.SYSTEM_BASE + "\n" + prompts.VISION_AGENT_PROMPT.format(
        equipment_kind=kind or "industrial equipment",
        equipment_id=equipment_id or "unspecified",
        checklist=checklist_block,
        region_vocabulary=region_block,
        signal_correlation_hint=signal_hint,
    )

    images: list[tuple[Path, str | None]] = []
    if image_path is not None and Path(image_path).exists():
        images.append((Path(image_path), "IMAGE 1 — 今回の点検写真"))
    for i, p in enumerate(extra_image_paths or [], start=2):
        if p and Path(p).exists():
            images.append((Path(p), f"IMAGE {i} — 追加アングル / 近接写真"))
    if reference_image_path is not None and Path(reference_image_path).exists():
        images.append((Path(reference_image_path),
                       f"IMAGE {len(images)+1} — REFERENCE NORMAL (同部位の正常時)"))

    user_text = (
        f"設備ID: {equipment_id or '(指定なし)'}\n"
        f"現場メモ: {inspection_memo or '(なし)'}\n"
        "上記コンテキストと添付画像を元に、JSON スキーマ通りに所見を返してください。"
    )

    if use_mock_mode() or not images:
        out = _mock_vision(risk, inspection_memo, equipment_id=equipment_id,
                           has_reference=reference_image_path is not None)
        _attach_evidence_images(out, primary_image=image_path)
        return AgentResult(name="Vision Inspection Agent", source="mock", output=out)
    try:
        client = client or LLMClient()
        raw = client.complete_with_images(
            system=system_prompt,
            user_text=user_text,
            images=images,
        )
        parsed = extract_json(raw)
        if not isinstance(parsed, dict):
            parsed = _mock_vision(risk, inspection_memo, equipment_id=equipment_id,
                                  has_reference=reference_image_path is not None)
        else:
            parsed = _normalize_vision_output(parsed, risk=risk, equipment_id=equipment_id,
                                              has_reference=reference_image_path is not None)
        _attach_evidence_images(parsed, primary_image=image_path)
        return AgentResult(name="Vision Inspection Agent", source="azure_openai", output=parsed)
    except Exception as exc:
        out = _mock_vision(risk, inspection_memo, equipment_id=equipment_id,
                           has_reference=reference_image_path is not None)
        _attach_evidence_images(out, primary_image=image_path)
        return AgentResult(name="Vision Inspection Agent", source="mock", output=out, error=str(exc))


def _vision_prompt_context(equipment_id: str | None) -> tuple[str, str, str]:
    from . import equipment_catalog
    if equipment_id is None:
        return "industrial equipment", "- 一般的な機械の外観異常を評価", "- general"
    try:
        spec = equipment_catalog.get(equipment_id)
        checklist = equipment_catalog.inspection_checklist(equipment_id)
        regions = equipment_catalog.region_vocabulary(equipment_id)
        return (
            f"{spec.kind} ({spec.label})",
            "\n".join(f"- {c}" for c in checklist),
            ", ".join(regions),
        )
    except KeyError:
        return "industrial equipment", "- 一般的な機械の外観異常を評価", "- general"


def _signal_correlation_hint(features: "SignalFeatures | None", risk: RiskAssessment) -> str:
    """Compose a 1-paragraph hint the Vision Agent can cross-reference. We
    *don't* include the raw numbers as authoritative — the wording emphasises
    that this is a complementary signal."""
    lines: list[str] = [f"ルールベース判定: {risk.risk_level} / 主要懸念: {risk.primary_concern}"]
    if features is not None:
        lines.append(
            f"主要数値: 振動RMS {features.vibration_rms:.3f}G, 最高温度 {features.temperature_max_c:.1f}℃, "
            f"最大音響 {features.sound_max_db:.1f}dB, 軸受帯エネルギー比 {features.bearing_band_energy_ratio:.2f}"
        )
        if features.fft_peaks:
            peaks = ", ".join(f"{p.frequency_hz:.0f}Hz" for p in features.fft_peaks[:3])
            lines.append(f"FFT主要ピーク: {peaks}")
    lines.append("→ これらと整合する視覚的特徴があれば signal_correlation で言及してください。")
    return "\n".join(lines)


def _normalize_vision_output(parsed: dict[str, Any], *, risk: RiskAssessment,
                              equipment_id: str | None, has_reference: bool) -> dict[str, Any]:
    """Guarantee every key the UI expects. The model can drop fields it
    doesn't have data for; we fill safe defaults so the renderers don't
    explode on a partial response."""
    from . import equipment_catalog
    parsed.setdefault("overview", "")
    parsed.setdefault("regions", [])
    parsed.setdefault("signal_correlation", "")
    parsed.setdefault("comparison_to_normal", "" if not has_reference else "（差分情報なし）")
    parsed.setdefault("confidence", "medium")
    parsed.setdefault("overall_confidence_score", 60)
    parsed.setdefault("visual_findings", [])
    parsed.setdefault("recommended_additional_shots", [])
    parsed.setdefault("human_confirmation_required", True)
    # Back-fill missing bboxes from the per-(kind, intensity) layout map so
    # the annotator has something accurate to draw even when the model
    # omits the bbox. Intensity is derived from the risk level so the
    # coordinates line up with the actual demo photo for that intensity.
    intensity = {"Normal": "normal", "Warning": "warning",
                 "Critical": "critical"}.get(risk.risk_level, "normal")
    for region in parsed["regions"]:
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            rid = region.get("region_id", "")
            region["bbox"] = list(equipment_catalog.default_bbox(
                equipment_id or "Pump-03", rid, intensity))
    # If `visual_findings` is empty, synthesize from regions for downstream
    # consumers (work_order / management_report) that still read the legacy key.
    if not parsed["visual_findings"] and parsed["regions"]:
        for r in parsed["regions"][:5]:
            if isinstance(r, dict):
                obs = r.get("observation") or r.get("region_id", "")
                if obs:
                    parsed["visual_findings"].append(obs)
    return parsed


def _attach_evidence_images(output: dict[str, Any], *, primary_image: Path | None) -> None:
    """Render bbox overlay + per-region crops + enhanced view and embed them
    as data URLs in ``output['evidence_images']``. Failures are silent — the
    text-only output still works."""
    if primary_image is None or not Path(primary_image).exists():
        return
    regions = output.get("regions") or []
    if not regions:
        return
    try:
        from . import vision_annotator
        annotation = vision_annotator.annotate_path(primary_image, regions)
        output["evidence_images"] = annotation
    except Exception as exc:  # pragma: no cover
        output["evidence_images"] = {"error": str(exc), "overlay": None,
                                      "enhanced": None, "crops": {}}


def _mock_vision(risk: RiskAssessment, memo: str, *,
                 equipment_id: str | None = None,
                 has_reference: bool = False) -> dict[str, Any]:
    """Deterministic mock that mirrors the upgraded vision-agent schema.
    The shape matches what the real LLM returns so the UI is exercised on
    both paths."""
    from . import equipment_catalog
    try:
        spec = equipment_catalog.get(equipment_id) if equipment_id else None
    except KeyError:
        spec = None
    kind = spec.kind if spec else "pump"

    # Severity per risk level.
    severity = {"Normal": "normal", "Warning": "minor",
                "Critical": "moderate"}.get(risk.risk_level, "minor")
    # On Critical, push the most-relevant region to "severe."
    promote_severe = risk.risk_level == "Critical"

    intensity = {"Normal": "normal", "Warning": "warning",
                 "Critical": "critical"}.get(risk.risk_level, "normal")

    # Per-(kind, intensity) regions to flag. We tune the picks so the
    # bboxes always land on parts that are actually visible in the
    # specific demo photo for that intensity — otherwise the annotator
    # ends up drawing a rectangle on empty floor.
    per_kind_intensity_regions: dict[str, dict[str, list[tuple[str, str]]]] = {
        "pump": {
            "normal": [
                ("casing-surface", "ケーシング外観に異常なし。塗装の劣化や油滲みは確認されない"),
                ("bearing-housing", "軸受ハウジング表面に異常な変色・油滲みなし"),
                ("pipe-flange", "吸込側フランジに漏れ跡なし、ボルト緩みなし"),
            ],
            "warning": [
                ("bearing-housing", "軸受ハウジングに薄い油滲み状の変色"),
                ("mechanical-seal", "メカニカルシール周辺に湿り跡の可能性"),
                ("bolt-upper-row", "上段固定ボルト 1〜2 本周辺に微小な錆びの兆候"),
            ],
            "critical": [
                ("drain-port", "ドレン周辺と床面に明確な油漏れ跡"),
                ("bearing-housing", "軸受ハウジング表面に複数箇所の油滲み・変色"),
                ("casing-surface", "ケーシングに広範な汚れ・腐食兆候"),
            ],
        },
        "motor": {
            "normal": [
                ("frame-surface", "モーターフレーム表面に塗装剥がれや異常なし"),
                ("terminal-box", "端子箱カバーに熱変色なし、ボルト緩みなし"),
                ("name-plate", "銘板の判読性良好"),
            ],
            "warning": [
                ("terminal-box", "端子箱カバーの塗装にわずかな熱変色"),
                ("cooling-fins", "冷却フィン間に細かい粉塵堆積"),
                ("frame-surface", "フレーム下部に微小な汚れ・油跡"),
            ],
            "critical": [
                ("frame-surface", "モーターフレーム全体に錆び・腐食が進行"),
                ("terminal-box", "端子箱周辺に変色 / シール材の劣化が見られる"),
                ("ventilation-slots", "通風口の一部に明確な塵詰まり"),
            ],
        },
        "fan": {
            "normal": [
                ("blade-hub", "羽根ハブ部に付着物なし、塗装剥がれなし"),
                ("v-belt", "Vベルトに摩耗・ひび割れの兆候なし"),
                ("bearing-housing", "軸受ハウジングに油滲み跡なし"),
            ],
            "warning": [
                ("v-belt", "Vベルト側面にひび割れの可能性"),
                ("blade-tip", "羽根先端に付着物（バランス変化の懸念）"),
                ("bearing-housing", "軸受ハウジングに油滲み跡"),
            ],
            "critical": [
                ("blade-hub", "羽根に大量の繊維状付着物が絡みついている"),
                ("guard-mesh", "ガードメッシュに広範な目詰まり"),
                ("bearing-housing", "軸受周辺に著しい汚れ・潤滑油の漏れ跡"),
            ],
        },
        "compressor": {
            "normal": [
                ("cylinder-head", "シリンダヘッドに油漏れ跡なし、塗装良好"),
                ("pressure-gauge", "圧力計の指示値が正常範囲内"),
                ("tank-surface", "タンク表面に錆び・凹みなし"),
            ],
            "warning": [
                ("oil-sight-glass", "覗き窓の油色が暗く変化している可能性"),
                ("safety-valve", "安全弁周辺に薄い油痕"),
                ("cooling-fins", "冷却フィンに汚れの堆積"),
            ],
            "critical": [
                ("cylinder-head", "シリンダヘッド周辺に明確な油漏れ・煤汚れ"),
                ("tank-surface", "タンク表面に広範な錆び・腐食"),
                ("pressure-gauge", "圧力計のガラス面に汚れ / 指針確認困難"),
            ],
        },
    }
    by_kind = per_kind_intensity_regions.get(kind, per_kind_intensity_regions["pump"])
    region_specs = by_kind.get(intensity) or by_kind["normal"]

    regions: list[dict[str, Any]] = []
    for i, (rid, obs) in enumerate(region_specs):
        sev = "severe" if (promote_severe and i == 0) else severity
        if risk.risk_level == "Normal":
            sev = "normal"
            obs_filled = obs.replace("可能性", "確認されず").replace("兆候", "なし").replace("懸念", "問題なし")
        else:
            obs_filled = obs
        conf = {"normal": 75, "minor": 60, "moderate": 78, "severe": 88}[sev]
        action = {
            "normal": "次回定期点検時に同位置再撮影",
            "minor": "近接撮影 + 経過観察",
            "moderate": "近接撮影 + 計測 (温度 / 触診)",
            "severe": "即時管理者報告 + 周辺領域の詳細撮影",
        }[sev]
        bbox = list(equipment_catalog.default_bbox(equipment_id or "Pump-03", rid, intensity))
        regions.append({
            "region_id": rid,
            "bbox": bbox,
            "observation": obs_filled,
            "severity": sev,
            "confidence_score": conf,
            "evidence": [obs_filled],
            "recommended_action": action,
        })

    overall_conf = 60 if risk.risk_level != "Critical" else 78
    signal_corr_msg = {
        "Normal": "センサー所見と一致。視覚的にも異常傾向は確認されません。",
        "Warning": f"視覚観察は {risk.primary_concern} と整合しており、軽度劣化を示唆します。",
        "Critical": f"視覚観察は {risk.primary_concern} と強く整合します。複合所見により早期点検を推奨します。",
    }[risk.risk_level]
    comparison = ""
    if has_reference:
        comparison = {
            "Normal": "参照写真との顕著な差は確認されません。",
            "Warning": "参照写真と比較し、対象部位に軽度の変色 / 汚れの進行が見られます。",
            "Critical": "参照写真と比較し、複数部位で明確な進行（変色・滲み）が確認されます。",
        }[risk.risk_level]

    shots_map = {
        "Normal": ["次回点検時の同位置撮影"],
        "Warning": [f"{regions[0]['region_id']} の近接画像（斜め方向）",
                    "全景の左右両側からのアングル"],
        "Critical": [f"{regions[0]['region_id']} の高解像度近接（複数アングル）",
                     "潤滑油受け部 / オイルパドル領域",
                     "対象周辺の温度分布が分かるサーモ画像"],
    }

    return {
        "overview": {
            "Normal": "全体的に外観異常は確認されません。",
            "Warning": "複数部位に軽度の変化が見られます。経過観察を推奨します。",
            "Critical": "複数部位で進行した劣化兆候が確認されます。",
        }[risk.risk_level],
        "regions": regions,
        "signal_correlation": signal_corr_msg,
        "comparison_to_normal": comparison,
        "overall_confidence_score": overall_conf,
        "confidence": "medium" if risk.risk_level != "Critical" else "high",
        "visual_findings": [r["observation"] for r in regions],
        "recommended_additional_shots": shots_map[risk.risk_level],
        "human_confirmation_required": True,
        "memo_reference": memo[:200],
    }


# ──────────────────────────────────────────────────────────────────────────
# Manual RAG Agent
# ──────────────────────────────────────────────────────────────────────────

def run_manual_rag_agent(features: SignalFeatures, risk: RiskAssessment, *, client: LLMClient | None = None) -> AgentResult:
    query_terms = build_query_from_findings(risk.primary_concern, risk.evidence_lines())
    hits = manual_search(query_terms, top_k=3)
    if use_mock_mode():
        return AgentResult(
            name="Manual RAG Agent",
            source="mock",
            output=_mock_manual_rag(features, risk, hits),
        )
    try:
        client = client or LLMClient()
        snippets = "\n\n".join(
            f"[{h.section_title}] (score={h.score})\n{h.body}" for h in hits
        ) or "(該当段落なし)"
        user = prompts.MANUAL_RAG_AGENT_PROMPT.format(
            signal_summary=json.dumps(risk.to_dict(), ensure_ascii=False, indent=2),
            manual_snippets=snippets,
        )
        raw = client.complete(prompts.SYSTEM_BASE, user)
        parsed = extract_json(raw) or _mock_manual_rag(features, risk, hits)
        if not isinstance(parsed, dict):
            parsed = _mock_manual_rag(features, risk, hits)
        # always include the retrieval evidence in the result so the UI can show it
        parsed.setdefault("retrieved_sections", [{"section": h.section_title, "score": h.score} for h in hits])
        return AgentResult(name="Manual RAG Agent", source="azure_openai", output=parsed)
    except Exception as exc:
        return AgentResult(name="Manual RAG Agent", source="mock", output=_mock_manual_rag(features, risk, hits), error=str(exc))


def _mock_manual_rag(features: SignalFeatures, risk: RiskAssessment, hits: list[RetrievalResult]) -> dict[str, Any]:
    rules: list[dict[str, str]] = []
    if features.vibration_rms >= 0.30:
        rules.append({
            "rule": "振動RMSが 0.30 G を超過した場合、即時点検対象とする（第1章 [1.3]）",
            "current_value": f"{features.vibration_rms:.3f} G",
            "judgement": "該当",
        })
    elif features.vibration_rms >= 0.20:
        rules.append({
            "rule": "振動RMSが 0.20 G を超過した場合、警告レベルとする（第1章 [1.2]）",
            "current_value": f"{features.vibration_rms:.3f} G",
            "judgement": "該当",
        })
    if features.temperature_max_c >= 50.0:
        rules.append({
            "rule": "軸受温度 50℃ 以上は Critical（第3章 [3.1]）",
            "current_value": f"{features.temperature_max_c:.1f} ℃",
            "judgement": "該当",
        })
    elif features.temperature_max_c >= 45.0:
        rules.append({
            "rule": "軸受温度 45℃ 以上は警告（第3章 [3.1]）",
            "current_value": f"{features.temperature_max_c:.1f} ℃",
            "judgement": "該当",
        })
    if features.sound_max_db >= 55:
        rules.append({
            "rule": "音響レベル 55dB 以上は Critical（第2章 [2.1]）",
            "current_value": f"{features.sound_max_db:.1f} dB",
            "judgement": "該当",
        })
    if features.bearing_band_energy_ratio >= 0.25:
        rules.append({
            "rule": "振動と温度が同時に上昇している場合は軸受摩耗または潤滑不良を疑う（第3章 [3.2]）",
            "current_value": f"100-300Hz エネルギー比 {features.bearing_band_energy_ratio:.2f}",
            "judgement": "該当",
        })
    if not rules:
        rules.append({
            "rule": "全指標が許容範囲内（第1〜4章）",
            "current_value": "全項目 Normal",
            "judgement": "該当",
        })

    procedure = [
        "1. 設備停止の可否を管理者に確認",
        "2. 軸受部の温度を赤外線温度計で測定",
        "3. 固定ボルトの締結状態を確認（規定 45 N·m）",
        "4. 異音発生箇所を聴音棒で特定",
        "5. 軸受周辺の漏れ・変色を目視確認",
    ] if risk.risk_level != "Normal" else [
        "1. 次回定期点検時に同位置を再確認",
        "2. 異常傾向が継続していないかトレンド監視",
    ]

    return {
        "applicable_rules": rules,
        "recommended_procedure_steps": procedure,
        "retrieved_sections": [{"section": h.section_title, "score": h.score} for h in hits],
    }


# ──────────────────────────────────────────────────────────────────────────
# Root Cause Agent
# ──────────────────────────────────────────────────────────────────────────

def run_root_cause_agent(
    signal_result: AgentResult,
    vision_result: AgentResult,
    manual_result: AgentResult,
    risk: RiskAssessment,
    history_summary: str,
    *,
    client: LLMClient | None = None,
) -> AgentResult:
    if use_mock_mode():
        return AgentResult(name="Root Cause Agent", source="mock", output=_mock_root_cause(risk))
    try:
        client = client or LLMClient()
        user = prompts.ROOT_CAUSE_AGENT_PROMPT.format(
            signal_summary=json.dumps(_strip_for_llm(signal_result.output), ensure_ascii=False, indent=2),
            vision_summary=json.dumps(_strip_for_llm(vision_result.output), ensure_ascii=False, indent=2),
            manual_summary=json.dumps(_strip_for_llm(manual_result.output), ensure_ascii=False, indent=2),
            history_summary=history_summary,
        )
        raw = client.complete(prompts.SYSTEM_BASE, user)
        parsed = extract_json(raw) or _mock_root_cause(risk)
        if not isinstance(parsed, dict):
            parsed = _mock_root_cause(risk)
        return AgentResult(name="Root Cause Agent", source="azure_openai", output=parsed)
    except Exception as exc:
        return AgentResult(name="Root Cause Agent", source="mock", output=_mock_root_cause(risk), error=str(exc))


def _mock_root_cause(risk: RiskAssessment) -> dict[str, Any]:
    if risk.risk_level == "Critical":
        return {
            "abnormality_summary": (
                f"{risk.primary_concern}を中心に複数指標が同時に悪化しており、"
                "軸受系の異常が強く疑われます。"
            ),
            "evidence": risk.evidence_lines(),
            "root_cause_hypotheses": [
                {
                    "cause": "軸受摩耗",
                    "likelihood": "high",
                    "reason": "振動・温度・100-300Hz帯エネルギーが同時に上昇しており、軸受摩耗の典型パターンに該当",
                    "additional_checks": ["軸受温度の継続測定", "潤滑油の劣化状態確認", "軸方向のガタ確認"],
                },
                {
                    "cause": "固定部の緩み",
                    "likelihood": "medium",
                    "reason": "振動の高調波成分増加は固定ボルトの緩みでも発生し得る",
                    "additional_checks": ["ボルト締結トルク確認（規定 45 N·m）", "基礎ベース取付状態確認"],
                },
                {
                    "cause": "潤滑不良",
                    "likelihood": "medium",
                    "reason": "温度上昇は潤滑切れによる摩擦増加でも生じる",
                    "additional_checks": ["潤滑油レベル確認", "グリスニップル状態確認"],
                },
            ],
            "uncertainty": "確定診断には現場での触診・聴音・潤滑油サンプリングが必要",
            "human_confirmation_required": True,
        }
    if risk.risk_level == "Warning":
        return {
            "abnormality_summary": (
                f"{risk.primary_concern}を伴う軽度の異常傾向。経過観察と追加確認を推奨。"
            ),
            "evidence": risk.evidence_lines(),
            "root_cause_hypotheses": [
                {
                    "cause": "軸受摩耗の初期",
                    "likelihood": "medium",
                    "reason": "100-300Hz帯のエネルギー比が通常より高い",
                    "additional_checks": ["軸受温度のトレンド監視", "次回点検時の振動再測定"],
                },
                {
                    "cause": "一時的負荷増加",
                    "likelihood": "medium",
                    "reason": "電流上昇と振動上昇が併発する場合、一時的な負荷増加でも観察される",
                    "additional_checks": ["直近の運転条件確認", "上流／下流機器の状態確認"],
                },
                {
                    "cause": "潤滑油の劣化",
                    "likelihood": "low",
                    "reason": "音響上昇のみでは断定不可。継続観察が必要",
                    "additional_checks": ["前回潤滑からの経過時間確認"],
                },
            ],
            "uncertainty": "Warningレベルのため、即時停止は不要。24〜72時間以内に追加確認を推奨",
            "human_confirmation_required": True,
        }
    if risk.ambiguity_flag:
        return {
            "abnormality_summary": (
                "明確な閾値超過はないが、断続的な兆候が観察されます。"
                "判断には継続観察または追加データが必要です。"
            ),
            "evidence": risk.evidence_lines() or ["平均値は正常範囲内、ただし短時間のピークあり"],
            "root_cause_hypotheses": [
                {
                    "cause": "判断困難（要追加情報）",
                    "likelihood": "low",
                    "reason": "現状のデータからは複数の解釈が可能",
                    "additional_checks": ["長時間連続データの取得", "現場での聴音確認", "次回定期点検時の比較"],
                },
            ],
            "uncertainty": "確信度低。AI による単独判断ではなく、現場確認を推奨",
            "human_confirmation_required": True,
        }
    return {
        "abnormality_summary": "現時点で明確な異常は検出されません。",
        "evidence": ["主要指標すべて正常範囲内"],
        "root_cause_hypotheses": [
            {
                "cause": "異常なし",
                "likelihood": "high",
                "reason": "ルールベース判定で全項目 Normal",
                "additional_checks": ["次回定期点検時のトレンド比較"],
            },
        ],
        "uncertainty": "現時点のスナップショットでの判断。長期トレンドは引き続き監視",
        "human_confirmation_required": False,
    }


# ──────────────────────────────────────────────────────────────────────────
# Action Planning Agent
# ──────────────────────────────────────────────────────────────────────────

def run_action_planning_agent(
    root_cause: AgentResult,
    inventory_summary: str,
    risk: RiskAssessment,
    *,
    client: LLMClient | None = None,
) -> AgentResult:
    if use_mock_mode():
        return AgentResult(name="Action Planning Agent", source="mock", output=_mock_action_plan(risk, root_cause))
    try:
        client = client or LLMClient()
        user = prompts.ACTION_PLANNING_AGENT_PROMPT.format(
            root_cause_summary=json.dumps(_strip_for_llm(root_cause.output), ensure_ascii=False, indent=2),
            inventory_summary=inventory_summary,
            risk_level=risk.risk_level,
        )
        raw = client.complete(prompts.SYSTEM_BASE, user)
        parsed = extract_json(raw) or _mock_action_plan(risk, root_cause)
        if not isinstance(parsed, dict):
            parsed = _mock_action_plan(risk, root_cause)
        return AgentResult(name="Action Planning Agent", source="azure_openai", output=parsed)
    except Exception as exc:
        return AgentResult(name="Action Planning Agent", source="mock", output=_mock_action_plan(risk, root_cause), error=str(exc))


def _mock_action_plan(risk: RiskAssessment, root_cause: AgentResult) -> dict[str, Any]:
    if risk.risk_level == "Critical":
        return {
            "priority": "Critical",
            "deadline_hours": 24,
            "work_steps": [
                "1. 設備停止可否を管理者に確認",
                "2. 軸受部の温度を赤外線温度計で測定（50℃超過なら即時停止判断）",
                "3. 固定ボルトの締結状態を確認（規定 45 N·m）",
                "4. 異音発生箇所を聴音棒で特定",
                "5. 軸受周辺の漏れ・変色を目視確認、写真記録",
                "6. 潤滑油レベルおよび状態確認",
                "7. 結果を点検記録に入力し、管理者に報告",
            ],
            "required_tools": ["トルクレンチ（10〜60 N·m）", "赤外線温度計", "聴音棒", "携帯型振動計"],
            "required_parts": ["軸受ユニット BRG-P03-6205ZZ（在庫2、予備として要確保）"],
            "safety_notes": [
                "回転部への接触禁止",
                "高温部に注意。赤外線温度計を使用し、直接触れない",
                "停止判断は必ず管理者承認の上で実施",
                "異音継続または温度上昇継続時は運転停止を検討",
            ],
            "manager_approval_required": True,
            "post_work_recording": [
                "点検実施日時、担当者",
                "各指標の測定値（振動・温度・音響）",
                "発見事項と写真",
                "実施した処置",
                "次回点検計画",
            ],
        }
    if risk.risk_level == "Warning":
        return {
            "priority": "High",
            "deadline_hours": 72,
            "work_steps": [
                "1. 軸受部の温度を赤外線温度計で測定",
                "2. 固定ボルトの締結状態を確認",
                "3. 異音発生箇所を聴音棒で特定",
                "4. 軸受周辺の目視確認、必要に応じて写真記録",
                "5. 結果を点検記録に入力",
            ],
            "required_tools": ["トルクレンチ", "赤外線温度計", "聴音棒"],
            "required_parts": ["（部品交換は現時点では不要）"],
            "safety_notes": [
                "回転部への接触禁止",
                "急激な悪化があれば即時停止判断を管理者に相談",
            ],
            "manager_approval_required": True,
            "post_work_recording": [
                "各指標の測定値",
                "目視所見",
                "次回点検時に比較するための写真",
            ],
        }
    if risk.ambiguity_flag:
        return {
            "priority": "Medium",
            "deadline_hours": 168,
            "work_steps": [
                "1. 長時間連続データを取得（最低1時間）",
                "2. 現場で聴音確認",
                "3. 次回定期点検時に比較するため写真を撮影",
                "4. トレンド監視を継続",
            ],
            "required_tools": ["振動計", "聴音棒", "カメラ"],
            "required_parts": [],
            "safety_notes": ["異常傾向が継続する場合は再評価"],
            "manager_approval_required": False,
            "post_work_recording": ["連続データのファイル名", "聴音所見"],
        }
    return {
        "priority": "Low",
        "deadline_hours": 720,
        "work_steps": [
            "1. 次回定期点検時に同位置の振動・温度を再測定",
            "2. トレンドを比較し、長期傾向を確認",
        ],
        "required_tools": ["振動計", "赤外線温度計"],
        "required_parts": [],
        "safety_notes": ["通常運転を継続"],
        "manager_approval_required": False,
        "post_work_recording": ["次回点検日時", "トレンド比較結果"],
    }


# ──────────────────────────────────────────────────────────────────────────
# What-if Simulator
# ──────────────────────────────────────────────────────────────────────────

def run_whatif_agent(risk: RiskAssessment, root_cause: AgentResult, *, client: LLMClient | None = None) -> AgentResult:
    if use_mock_mode():
        return AgentResult(name="What-if Simulator", source="mock", output=_mock_whatif(risk))
    try:
        client = client or LLMClient()
        situation = {
            "risk_level": risk.risk_level,
            "health_score": risk.health_score,
            "primary_concern": risk.primary_concern,
            "root_cause": _strip_for_llm(root_cause.output),
        }
        user = prompts.WHATIF_AGENT_PROMPT.format(
            situation_summary=json.dumps(situation, ensure_ascii=False, indent=2),
        )
        raw = client.complete(prompts.SYSTEM_BASE, user)
        parsed = extract_json(raw) or _mock_whatif(risk)
        if not isinstance(parsed, dict):
            parsed = _mock_whatif(risk)
        return AgentResult(name="What-if Simulator", source="azure_openai", output=parsed)
    except Exception as exc:
        return AgentResult(name="What-if Simulator", source="mock", output=_mock_whatif(risk), error=str(exc))


def _mock_whatif(risk: RiskAssessment) -> dict[str, Any]:
    if risk.risk_level == "Critical":
        return {
            "scenarios": [
                {
                    "name": "今すぐ点検（24時間以内）",
                    "timing": "24時間以内",
                    "predicted_risk": "low",
                    "production_impact": "計画的な短時間停止で対応可能",
                    "recommended": True,
                    "rationale": "現在 Critical レベル。早期介入により二次損傷と長時間停止を回避",
                },
                {
                    "name": "3日後に点検",
                    "timing": "72時間",
                    "predicted_risk": "medium",
                    "production_impact": "監視継続で対応可能だが、計画外停止リスクあり",
                    "recommended": False,
                    "rationale": "悪化速度によっては計画外停止に至る可能性",
                },
                {
                    "name": "1週間放置",
                    "timing": "168時間",
                    "predicted_risk": "high",
                    "production_impact": "故障・生産停止の可能性が大幅に増加",
                    "recommended": False,
                    "rationale": "軸受系異常が疑われるため、放置は強く非推奨",
                },
            ]
        }
    if risk.risk_level == "Warning":
        return {
            "scenarios": [
                {
                    "name": "今すぐ点検",
                    "timing": "24時間以内",
                    "predicted_risk": "low",
                    "production_impact": "短時間で確認可能",
                    "recommended": True,
                    "rationale": "悪化前に原因を特定すれば計画保全で済む",
                },
                {
                    "name": "次回定期点検まで監視",
                    "timing": "72時間",
                    "predicted_risk": "medium",
                    "production_impact": "監視継続で対応可能",
                    "recommended": True,
                    "rationale": "Warning レベルのため、監視強化と組合せれば許容範囲",
                },
                {
                    "name": "1週間放置",
                    "timing": "168時間",
                    "predicted_risk": "medium",
                    "production_impact": "悪化次第で計画外停止の可能性",
                    "recommended": False,
                    "rationale": "傾向監視なしの放置は推奨しない",
                },
            ]
        }
    return {
        "scenarios": [
            {
                "name": "通常運転継続",
                "timing": "次回定期点検",
                "predicted_risk": "low",
                "production_impact": "影響なし",
                "recommended": True,
                "rationale": "全指標 Normal。通常運転を継続",
            },
            {
                "name": "前倒し点検",
                "timing": "1週間以内",
                "predicted_risk": "low",
                "production_impact": "短時間停止が必要",
                "recommended": False,
                "rationale": "現時点で前倒しの必要性は薄い",
            },
            {
                "name": "計画変更なし＋トレンド監視",
                "timing": "継続",
                "predicted_risk": "low",
                "production_impact": "影響なし",
                "recommended": True,
                "rationale": "現状維持で十分。トレンドのみ確認",
            },
        ]
    }


# ──────────────────────────────────────────────────────────────────────────
# Governance Agent — pipeline tail. Reads every upstream agent's verdict
# and produces a single "what must the human verify before approving?" gate.
# ──────────────────────────────────────────────────────────────────────────

def run_governance_agent(
    *,
    intake: AgentResult,
    signal: AgentResult,
    vision: AgentResult,
    manual_rag: AgentResult,
    root_cause: AgentResult,
    action_plan: AgentResult,
    whatif: AgentResult,
    risk: RiskAssessment,
    client: LLMClient | None = None,
) -> AgentResult:
    """Run the Governance Agent. This is the *last* step before human
    approval — it sanitises the upstream outputs and produces an audit-ready
    summary of (a) confidence, (b) what the manager must verify, (c) which
    agents fell back to mock so the human knows where AI couldn't reach."""
    fallback_used = [a.name for a in (intake, signal, vision, manual_rag,
                                       root_cause, action_plan, whatif)
                     if a.source != "azure_openai" and not use_mock_mode()]
    pipeline_summary = {
        "risk": risk.to_dict(),
        "intake": _strip_for_llm(intake.output),
        "signal": _strip_for_llm(signal.output),
        "vision": _strip_for_llm(vision.output),
        "manual_rag": _strip_for_llm(manual_rag.output),
        "root_cause": _strip_for_llm(root_cause.output),
        "action_plan": _strip_for_llm(action_plan.output),
        "whatif": _strip_for_llm(whatif.output),
        "fallback_used": fallback_used,
    }
    if use_mock_mode():
        return AgentResult(
            name="Governance Agent", source="mock",
            output=_mock_governance(risk, intake, vision, root_cause,
                                     action_plan, fallback_used=[]),
        )
    try:
        client = client or LLMClient()
        user = prompts.GOVERNANCE_AGENT_PROMPT.format(
            pipeline_summary=json.dumps(pipeline_summary, ensure_ascii=False, indent=2),
        )
        raw = client.complete(prompts.SYSTEM_BASE, user)
        parsed = extract_json(raw) or _mock_governance(
            risk, intake, vision, root_cause, action_plan,
            fallback_used=fallback_used)
        if not isinstance(parsed, dict):
            parsed = _mock_governance(risk, intake, vision, root_cause,
                                       action_plan, fallback_used=fallback_used)
        # Always trust our own bookkeeping over what the LLM claims.
        parsed["fallback_used"] = fallback_used
        return AgentResult(name="Governance Agent", source="azure_openai", output=parsed)
    except Exception as exc:
        return AgentResult(
            name="Governance Agent", source="mock",
            output=_mock_governance(risk, intake, vision, root_cause,
                                     action_plan, fallback_used=fallback_used),
            error=str(exc),
        )


def _mock_governance(
    risk: RiskAssessment,
    intake: AgentResult,
    vision: AgentResult,
    root_cause: AgentResult,
    action_plan: AgentResult,
    *,
    fallback_used: list[str],
) -> dict[str, Any]:
    intake_out = intake.output if isinstance(intake.output, dict) else {}
    vision_out = vision.output if isinstance(vision.output, dict) else {}
    rc_out = root_cause.output if isinstance(root_cause.output, dict) else {}
    plan_out = action_plan.output if isinstance(action_plan.output, dict) else {}

    drivers: list[str] = []
    checkpoints: list[str] = []
    safety: list[str] = []

    if risk.risk_level == "Critical":
        confidence = "high" if not fallback_used else "medium"
        drivers.append("複数指標が同時に閾値を超過")
        checkpoints += [
            "停止判断の最終承認（安全上の影響範囲を確認）",
            "下流設備への波及対象がリストアップされているか",
        ]
        safety += [
            "回転部・高温部への接触禁止",
            "設備停止は必ず管理者承認の上で実施",
        ]
    elif risk.risk_level == "Warning":
        confidence = "medium"
        drivers.append("単一指標が警告レベル / 別指標は正常 — 経過観察と整合確認が必要")
        checkpoints += [
            "追加点検のタイミング（24〜72時間以内）の承認",
            "計画外停止リスクの許容可否を判断",
        ]
        safety += ["急変時の即時停止判断の合意"]
    else:
        confidence = "high"
        checkpoints += ["次回点検時のトレンド比較計画"]

    if intake_out.get("data_quality") in ("degraded", "acceptable"):
        drivers.append(f"入力データ品質: {intake_out.get('data_quality')}")
    if not vision_out.get("regions"):
        drivers.append("Vision Agent が領域所見を生成していない")
    if rc_out.get("uncertainty"):
        drivers.append(rc_out["uncertainty"])

    manager_required = bool(plan_out.get("manager_approval_required", True)) \
        or risk.risk_level != "Normal"
    auto_executable = (
        risk.risk_level == "Normal"
        and not fallback_used
        and not drivers
    )

    audit = (
        f"{risk.risk_level} 判定 — 主要懸念「{risk.primary_concern}」。"
        f"信頼度 {confidence}。"
        + (f" モックフォールバック: {', '.join(fallback_used)}。" if fallback_used else "")
    )

    return {
        "overall_confidence": confidence,
        "uncertainty_drivers": drivers or ["特になし"],
        "human_approval_required": manager_required,
        "approval_checkpoints": checkpoints,
        "safety_constraints": safety or ["通常の安全規定を遵守"],
        "auto_executable": auto_executable,
        "fallback_used": fallback_used,
        "audit_notes": audit,
    }


# ──────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    signal: AgentResult
    vision: AgentResult
    manual_rag: AgentResult
    root_cause: AgentResult
    action_plan: AgentResult
    whatif: AgentResult
    risk: RiskAssessment
    started_at: str = ""
    finished_at: str = ""
    equipment_id: str = ""
    # Optional book-ends. Default-None so old callers/tests that build a
    # PipelineResult directly keep working.
    intake: AgentResult | None = None
    governance: AgentResult | None = None

    @property
    def agents(self) -> list[AgentResult]:
        ordered: list[AgentResult] = []
        if self.intake is not None:
            ordered.append(self.intake)
        ordered.extend([self.signal, self.vision, self.manual_rag,
                        self.root_cause, self.action_plan, self.whatif])
        if self.governance is not None:
            ordered.append(self.governance)
        return ordered

    @property
    def total_elapsed_ms(self) -> int:
        return sum(a.elapsed_ms for a in self.agents)

    @property
    def azure_count(self) -> int:
        return sum(1 for a in self.agents if a.source == "azure_openai")

    @property
    def mock_count(self) -> int:
        return sum(1 for a in self.agents if a.source == "mock")

    @property
    def ai_health_score(self) -> int:
        """AI-derived health score: a rule-based score adjusted by the AI
        evidence. Vision severity and root-cause confidence both contribute,
        but weights are *bounded* — the AI shifts the number by ~±15 points
        max so a Critical setup doesn't clamp every equipment at 0 and a
        Normal setup doesn't peg every equipment at 100. That visible
        variation across equipment is the whole point.
        """
        return _blend_ai_score(
            base=self.risk.health_score,
            risk_level=self.risk.risk_level,
            equipment_id=self._equipment_id_hint(),
            vision_output=self.vision.output if isinstance(self.vision.output, dict) else {},
            root_cause_output=self.root_cause.output if isinstance(self.root_cause.output, dict) else {},
        )

    def _equipment_id_hint(self) -> str:
        """Best-effort lookup of the equipment_id used by this run, so the
        deterministic id-based jitter in :func:`_blend_ai_score` is stable."""
        if self.equipment_id:
            return self.equipment_id
        for source in (self.vision.output, self.signal.output):
            if isinstance(source, dict):
                eid = source.get("equipment_id")
                if isinstance(eid, str):
                    return eid
        return ""

    def as_combined_summary(self) -> dict[str, Any]:
        return {
            "risk": self.risk.to_dict(),
            "signal": self.signal.output,
            "vision": self.vision.output,
            "manual_rag": self.manual_rag.output,
            "root_cause": self.root_cause.output,
            "action_plan": self.action_plan.output,
        }


def _blend_ai_score(*, base: int, risk_level: str, equipment_id: str,
                     vision_output: dict[str, Any],
                     root_cause_output: dict[str, Any]) -> int:
    """Single source of truth for the AI-blended health score.

    Used by both :pyattr:`PipelineResult.ai_health_score` (real run) and
    :func:`predicted_ai_score` (sidebar preview for equipment without a
    pipeline yet). Both paths use the same numbers so the sidebar and the
    hero never disagree on the active equipment.

    Magnitudes are bounded: a single agent run shifts the score by at most
    ~±15 points so Critical runs don't collapse to 0 and Normal runs don't
    saturate at 100 — the differentiation between equipment is preserved.
    """
    val = float(base)

    # Vision severity — primary signal but capped at ±9.
    sev_weight = {"severe": -3.0, "moderate": -2.0, "minor": -1.0, "normal": 0.5}
    sev_contrib = 0.0
    for r in vision_output.get("regions", []) or []:
        if isinstance(r, dict):
            sev_contrib += sev_weight.get(str(r.get("severity", "")).lower(), 0.0)
    val += max(-9.0, min(9.0, sev_contrib))

    # Root cause likelihood — ±2 nudge based on the top hypothesis.
    hyps = root_cause_output.get("root_cause_hypotheses") or []
    if hyps and isinstance(hyps[0], dict):
        lvl = str(hyps[0].get("likelihood", "")).lower()
        val += {"high": -2.0, "medium": -1.0, "low": 0.0}.get(lvl, 0.0)

    # Vision confidence bump (only when not Critical so we don't mask a fault).
    if risk_level != "Critical":
        conf = vision_output.get("overall_confidence_score")
        if isinstance(conf, (int, float)) and conf >= 80:
            val += 1.5

    # Stable per-equipment offset so two equipment with identical rule scores
    # don't show the same AI number. ±3 range, deterministic from id.
    if equipment_id:
        h = sum(ord(c) for c in equipment_id) % 7
        val += (h - 3)

    return max(0, min(100, int(round(val))))


def predicted_ai_score(equipment_id: str, intensity: str, base_health: int,
                        risk_level: str) -> int:
    """Cheap, deterministic AI-style score predictor for the equipment list.

    The sidebar lists every equipment but we only run the full multi-agent
    pipeline on the one the user actually selects. To keep the AI column
    coherent across the whole list, we approximate what the Vision agent
    *would* say by reading the mock-vision severity layout for the same
    (equipment, intensity) pair and applying the same blending formula used
    by :meth:`PipelineResult.ai_health_score`. The active equipment with a
    fresh pipeline overrides this with the real number.
    """
    from .risk_engine import RiskAssessment

    risk_stub = RiskAssessment(
        risk_level=risk_level,
        health_score=base_health,
        findings=[],
        ambiguity_flag=False,
        primary_concern="",
    )
    try:
        mock = _mock_vision(risk_stub, "", equipment_id=equipment_id, has_reference=False)
    except Exception:
        mock = {}
    return _blend_ai_score(
        base=base_health,
        risk_level=risk_level,
        equipment_id=equipment_id,
        vision_output=mock if isinstance(mock, dict) else {},
        root_cause_output={},
    )


def run_pipeline(
    *,
    features: SignalFeatures,
    risk: RiskAssessment,
    image_path: Path | None,
    inspection_memo: str,
    history_summary: str,
    inventory_summary: str,
    client: LLMClient | None = None,
    progress: Callable[[str], None] | None = None,
    equipment_id: str | None = None,
    extra_image_paths: list[Path] | None = None,
    reference_image_path: Path | None = None,
) -> PipelineResult:
    """Run all agents in sequence. `progress` is an optional callback the UI
    uses to stream "Step N: ..." updates."""
    def step(msg: str) -> None:
        if progress is not None:
            progress(msg)

    def _timed(fn: Callable[[], AgentResult]) -> AgentResult:
        t0 = time.perf_counter()
        result = fn()
        result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return result

    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    step("Step 1: Intake Agent — 入力データを検証中…")
    intake = _timed(lambda: run_intake_agent(
        features, risk,
        equipment_id=equipment_id,
        image_path=image_path,
        extra_image_paths=extra_image_paths,
        reference_image_path=reference_image_path,
        inspection_memo=inspection_memo,
        client=client,
    ))

    step("Step 2: Signal Insight Agent — センサー解析を要約中…")
    signal = _timed(lambda: run_signal_agent(features, risk, client=client))

    step("Step 3: Vision Inspection Agent — 点検写真を分析中…")
    vision = _timed(lambda: run_vision_agent(
        image_path, inspection_memo, risk,
        client=client,
        equipment_id=equipment_id,
        extra_image_paths=extra_image_paths,
        reference_image_path=reference_image_path,
        features=features,
    ))

    step("Step 4: Manual RAG Agent — マニュアル根拠を検索中…")
    manual = _timed(lambda: run_manual_rag_agent(features, risk, client=client))

    step("Step 5: Root Cause Agent — 原因仮説を生成中…")
    root_cause = _timed(lambda: run_root_cause_agent(signal, vision, manual, risk, history_summary, client=client))

    step("Step 6: Action Planning Agent — 作業指示を作成中…")
    action_plan = _timed(lambda: run_action_planning_agent(root_cause, inventory_summary, risk, client=client))

    step("Step 7: What-if Simulator — 対応シナリオを比較中…")
    whatif = _timed(lambda: run_whatif_agent(risk, root_cause, client=client))

    step("Step 8: Governance Agent — 承認ゲートを準備中…")
    governance = _timed(lambda: run_governance_agent(
        intake=intake, signal=signal, vision=vision, manual_rag=manual,
        root_cause=root_cause, action_plan=action_plan, whatif=whatif,
        risk=risk, client=client,
    ))

    finished_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    step("完了")
    return PipelineResult(
        signal=signal,
        vision=vision,
        manual_rag=manual,
        root_cause=root_cause,
        action_plan=action_plan,
        whatif=whatif,
        risk=risk,
        started_at=started_at,
        finished_at=finished_at,
        equipment_id=equipment_id or "",
        intake=intake,
        governance=governance,
    )


# ──────────────────────────────────────────────────────────────────────────
# Follow-up Q&A — let a human interrogate the completed analysis
# ──────────────────────────────────────────────────────────────────────────

def run_followup_qa(question: str, result: PipelineResult, *, client: LLMClient | None = None) -> dict[str, Any]:
    """Answer a free-text follow-up question grounded ONLY in the pipeline
    output. Live mode routes to Azure OpenAI; otherwise a deterministic,
    grounded mock answers from the actual result data so the feature still
    demos offline (the answer is never generic — it cites real findings)."""
    if use_mock_mode():
        return _mock_followup_qa(question, result)
    try:
        client = client or LLMClient()
        raw = client.complete(
            system=prompts.SYSTEM_BASE,
            user=prompts.FOLLOWUP_QA_PROMPT.format(
                combined_summary=json.dumps(result.as_combined_summary(), ensure_ascii=False, indent=2),
                question=question,
            ),
            json_mode=True,
        )
        parsed = extract_json(raw)
        if isinstance(parsed, dict) and parsed.get("answer"):
            parsed.setdefault("grounded_in", [])
            parsed.setdefault("confidence", "medium")
            parsed.setdefault("human_confirmation_required", True)
            return parsed
    except Exception:
        pass
    return _mock_followup_qa(question, result)


def _mock_followup_qa(question: str, result: PipelineResult) -> dict[str, Any]:
    """Deterministic, grounded answer keyed on the question's intent. Pulls
    real numbers from the pipeline so the offline demo stays credible."""
    q = (question or "").lower()
    risk = result.risk
    rc = result.root_cause.output if isinstance(result.root_cause.output, dict) else {}
    plan = result.action_plan.output if isinstance(result.action_plan.output, dict) else {}
    whatif = result.whatif.output if isinstance(result.whatif.output, dict) else {}
    gov = result.governance.output if (result.governance and isinstance(result.governance.output, dict)) else {}
    eqid = result.equipment_id or "対象設備"

    def has(*words: str) -> bool:
        return any(w in q for w in words)

    # Cost / ROI
    if has("コスト", "金額", "円", "いくら", "損失", "roi", "費用", "回避"):
        from . import business_case
        impact = business_case.estimate(eqid, risk.risk_level)
        return {
            "answer": impact.headline_one_liner()
            + "（前提: " + " / ".join(impact.assumptions[:1]) + "）。最終的な発注・停止判断は管理者承認の上で。",
            "grounded_in": ["business_case", "failure_history.csv", "parts_inventory.csv"],
            "confidence": "medium" if risk.risk_level != "Normal" else "low",
            "human_confirmation_required": risk.risk_level != "Normal",
        }
    # Why / cause
    if has("なぜ", "原因", "why", "cause", "理由"):
        hyps = rc.get("root_cause_hypotheses") or []
        lines = [f"{h.get('cause','?')}（尤度 {h.get('likelihood','?')}）: {h.get('reason','')}"
                 for h in hyps[:3] if isinstance(h, dict)]
        body = "／ ".join(lines) if lines else "明確な原因は特定されていません。"
        return {
            "answer": f"{eqid} の原因仮説は次のとおりです: {body} いずれも確定診断には現場確認が必要です。",
            "grounded_in": ["Root Cause Agent", "Signal Insight Agent"],
            "confidence": "medium",
            "human_confirmation_required": bool(rc.get("human_confirmation_required", True)),
        }
    # When / deadline
    if has("いつ", "期限", "deadline", "when", "急", "何時間"):
        d = plan.get("deadline_hours")
        pr = plan.get("priority", risk.risk_level)
        return {
            "answer": f"推奨対応期限は {d} 時間以内（優先度 {pr}）です。"
            + ("管理者承認の上で実施してください。" if plan.get("manager_approval_required", True) else ""),
            "grounded_in": ["Action Planning Agent"],
            "confidence": "high" if d is not None else "low",
            "human_confirmation_required": bool(plan.get("manager_approval_required", True)),
        }
    # What-if / leaving it
    if has("放置", "what if", "シナリオ", "3日", "1週間", "予測", "scenario"):
        scen = whatif.get("scenarios", []) if isinstance(whatif, dict) else []
        lines = [f"{s.get('name','?')}→予測リスク {s.get('predicted_risk','?')}"
                 f"（{'推奨' if s.get('recommended') else '非推奨'}）"
                 for s in scen if isinstance(s, dict)]
        return {
            "answer": "対応タイミング別の予測: " + "／ ".join(lines) if lines
            else "What-if シナリオは生成されていません。",
            "grounded_in": ["What-if Simulator"],
            "confidence": "medium",
            "human_confirmation_required": False,
        }
    # Parts / stock
    if has("部品", "在庫", "parts", "調達", "発注"):
        from . import business_case
        impact = business_case.estimate(eqid, risk.risk_level)
        if impact.parts:
            p = impact.parts[0]
            return {
                "answer": f"主要部品: {p.name}（{p.part_id}, ¥{p.unit_price_jpy:,}）。{p.availability_note}。"
                + ("在庫不足のため早期手配を推奨します。" if (p.out_of_stock or p.below_reorder) else ""),
                "grounded_in": ["business_case", "parts_inventory.csv", "Action Planning Agent"],
                "confidence": "high",
                "human_confirmation_required": True,
            }
        return {
            "answer": "この設備の交換部品は現時点で特定されていません（部品交換不要の可能性）。",
            "grounded_in": ["Action Planning Agent"],
            "confidence": "low",
            "human_confirmation_required": False,
        }
    # Confidence / uncertainty
    if has("確信", "信頼", "不確実", "confidence", "大丈夫", "確か"):
        return {
            "answer": f"総合信頼度は {gov.get('overall_confidence', '不明')}。"
            f"{eqid} は {risk.risk_level} 判定で、自動実行は{'可' if gov.get('auto_executable') else '不可（人間承認ゲート）'}。"
            f"主な不確実性: {('、'.join(gov.get('uncertainty_drivers', [])[:2]) or '特になし')}。",
            "grounded_in": ["Governance Agent"],
            "confidence": str(gov.get("overall_confidence", "medium")).lower() if gov.get("overall_confidence") else "medium",
            "human_confirmation_required": bool(gov.get("human_approval_required", True)),
        }
    # Default — point back to the headline verdict.
    return {
        "answer": f"{eqid} は現在 {risk.risk_level} 判定（主要懸念: {risk.primary_concern}、"
        f"ヘルススコア {risk.health_score}/100）。具体的に『原因』『期限』『コスト』『放置した場合』『部品』などを"
        "質問いただければ、解析結果に基づいて回答します。",
        "grounded_in": ["Risk Engine", "Governance Agent"],
        "confidence": "low",
        "human_confirmation_required": True,
    }
