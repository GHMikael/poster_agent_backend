from typing import Dict, List

from pptx.util import Inches

from app.models import Panel


def classify_panel(section: str) -> str:
    s = (section or "").lower()

    if any(k in s for k in ["motivation", "background", "why", "introduction", "problem", "为什么", "背景"]):
        return "motivation"
    if any(k in s for k in ["method", "framework", "approach", "pipeline", "model", "workflow", "方案", "方法"]):
        return "method"
    if any(k in s for k in ["benchmark", "dataset", "data", "数据"]):
        return "benchmark"
    if any(k in s for k in ["innovation", "contribution", "novelty", "创新"]):
        return "innovation"
    if any(k in s for k in ["result", "experiment", "evaluation", "performance", "实验", "结果", "评估"]):
        return "results"
    if any(k in s for k in ["conclusion", "takeaway", "finding", "discussion", "inspiration", "结论", "启发"]):
        return "takeaway"
    return "general"


def sort_panels_for_dashboard(panels: List[Panel]) -> List[Panel]:
    priority = {
        "motivation": 1,
        "method": 2,
        "benchmark": 3,
        "innovation": 4,
        "results": 5,
        "takeaway": 6,
        "general": 7,
    }
    return sorted(panels, key=lambda p: priority.get(classify_panel(p.section), 99))


def dashboard_layout(prs, panels: List[Panel]) -> Dict[str, Dict]:
    slide_w = prs.slide_width
    slide_h = prs.slide_height

    margin = Inches(0.12)
    gap = Inches(0.10)
    header_h = Inches(0.78)
    footer_h = Inches(0.36)

    body_top = header_h + Inches(0.08)
    body_h = slide_h - header_h - footer_h - Inches(0.20)
    left_w = Inches(3.28)
    right_w = Inches(3.28)
    mid_w = slide_w - left_w - right_w - 2 * margin - 2 * gap
    row_h = (body_h - gap) / 2

    x_left = margin
    x_mid = margin + left_w + gap
    x_right = x_mid + mid_w + gap
    y_top = body_top
    y_bottom = body_top + row_h + gap

    positions = [
        {"x": x_left, "y": y_top, "w": left_w, "h": row_h},
        {"x": x_mid, "y": y_top, "w": mid_w, "h": row_h},
        {"x": x_right, "y": y_top, "w": right_w, "h": row_h},
        {"x": x_left, "y": y_bottom, "w": left_w, "h": row_h},
        {"x": x_mid, "y": y_bottom, "w": mid_w, "h": row_h},
        {"x": x_right, "y": y_bottom, "w": right_w, "h": row_h},
    ]

    layout = {}
    for idx, panel in enumerate(sort_panels_for_dashboard(panels)[:6]):
        layout[panel.section] = positions[idx]
    return layout


def choose_panel_icon(section: str) -> str:
    icons = {
        "motivation": "?",
        "method": "*",
        "benchmark": "#",
        "innovation": "+",
        "results": "=",
        "takeaway": ">",
        "general": "-",
    }
    return icons.get(classify_panel(section), "-")


def choose_accent_color_name(section: str) -> str:
    colors = {
        "motivation": "blue",
        "method": "blue",
        "benchmark": "orange",
        "innovation": "blue",
        "results": "green",
        "takeaway": "purple",
        "general": "blue",
    }
    return colors.get(classify_panel(section), "blue")
