"""CI guard for the agent-quality eval matrix.

Imports the harness in scripts/run_eval.py and asserts every one of the 20
presets is fully policy-compliant. If a future change breaks an agent's
policy behaviour (e.g. a Critical work order stops requiring approval), this
test fails instead of the regression shipping silently.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import run_eval  # noqa: E402


def test_all_presets_are_policy_compliant():
    results = run_eval.evaluate_all()
    assert len(results) == 20, f"expected 20 presets, got {len(results)}"

    failures = [
        f"{r.equipment_id}:{r.intensity} -> "
        + ", ".join(name for name, ok in r.checks.items() if not ok)
        for r in results
        if not r.passed
    ]
    assert not failures, "policy-noncompliant presets:\n" + "\n".join(failures)


def test_every_preset_runs_eight_agents():
    results = run_eval.evaluate_all()
    assert all(r.checks.get("8エージェント構造") for r in results)


def test_matrix_renders_markdown():
    md = run_eval.format_markdown_matrix(run_eval.evaluate_all())
    assert "評価マトリクス" in md
    assert "20/20" in md  # all presets must pass for this string to appear
