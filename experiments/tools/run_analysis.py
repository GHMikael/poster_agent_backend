"""Utilities for analysing archived visual-feedback poster runs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


def analyse_run_report(path: str | Path) -> Dict[str, Any]:
    report_path = Path(path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    iterations = report.get("iterations", []) or []
    summary = report.get("summary", {}) or {}

    global_issue_counter: Counter[str] = Counter()
    panel_issue_counter: Counter[str] = Counter()
    action_counter: Counter[str] = Counter()
    score_curve: List[float] = []
    issue_curve: List[int] = []
    template_curve: List[str] = []
    theme_curve: List[str] = []
    variant_curve: List[str] = []

    for record in iterations:
        feedback = record.get("feedback", {}) or {}
        task_snapshot = record.get("task_snapshot", {}) or {}
        globals_ = feedback.get("global_issues", []) or []
        panels = feedback.get("panel_feedback", []) or []
        global_issue_counter.update(globals_)
        issue_count = len(globals_)
        for panel in panels:
            issues = panel.get("issues", []) or []
            panel_issue_counter.update(issues)
            issue_count += len(issues)
            action_counter.update([panel.get("suggested_action", "none")])

        score_curve.append(float(record.get("score", 0.0)))
        issue_curve.append(issue_count)
        template_curve.append(task_snapshot.get("template", ""))
        theme_curve.append(task_snapshot.get("color_theme", ""))
        variant_curve.append(task_snapshot.get("layout_variant", ""))

    suggestions: List[str] = []
    if len(set(score_curve)) == 1 and len(score_curve) > 1:
        suggestions.append("Score is stagnant; trigger template/layout_variant exploration after the first tie.")
    if global_issue_counter.get("empty_space", 0) >= 2:
        suggestions.append("Repeated empty_space: increase emphasis_level, use spotlight/zigzag layout, add metric pills or takeaway rail.")
    if global_issue_counter.get("low_contrast", 0) >= 2:
        suggestions.append("Repeated low_contrast: rotate palette and strengthen panel header/accent contrast.")
    if action_counter.get("enlarge_font", 0) >= 3:
        suggestions.append("Many enlarge_font actions: prefer layout compaction or content enrichment over only scaling fonts.")
    if not suggestions:
        suggestions.append("Run is stable; next gains likely come from richer template design and content selection.")

    return {
        "run_id": report.get("run_id"),
        "folder": report.get("folder"),
        "best_score": summary.get("best_score"),
        "convergence_reason": summary.get("convergence_reason"),
        "score_curve": score_curve,
        "issue_curve": issue_curve,
        "global_issue_counts": dict(global_issue_counter),
        "panel_issue_counts": dict(panel_issue_counter),
        "action_counts": dict(action_counter),
        "template_curve": template_curve,
        "theme_curve": theme_curve,
        "variant_curve": variant_curve,
        "suggestions": suggestions,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Analyse a Paper2Poster run_report.json")
    parser.add_argument("run_report")
    args = parser.parse_args()
    print(json.dumps(analyse_run_report(args.run_report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
