"""Ours — free-form feedback ablation(E1 对照臂 / B5).

与 ``ours_svfp`` 的**唯一区别**:把 SVFP 的"封闭 schema 反馈 + 确定性 apply"
换成"自由文本 critique + LLM best-effort 重写"。其余——同一个 Qwen-VL、同样的
``generate_dashboard_pptx`` + ``render_pptx_to_png`` 渲染、同样的 max_iterations、
同一份 planner_cache——全部相同,以便 E1 干净地对比 *closed-set vs free-form*。

两处"打开封闭":
  1. critique:``vlm_chat(json_mode=False)`` 让 VLM 写自由散文,不约束 4 类 issue / JSON。
  2. apply:LLM 读 critique + 当前面板,best-effort 重写面板(而非确定性 dispatch)。

产出 E1 的对比指标(写入 metadata.config + freeform_trace.json):
  * action_executability —— 自由 critique 被成功转成**有效** task 编辑的比例
  * n_executed / n_attempts / convergence_reason
  * content_drift —— 是否改动了 bullet 文本(SVFP 永不改;free-form 可能乱改)
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import QWEN_VL_MODEL
from app.feedback_loop import render_pptx_to_png
from app.models import PosterTask
from app.ppt_renderer import generate_dashboard_pptx
from experiments.baselines.base import BaselineRunner, PosterArtifact
from experiments.baselines._planner_shared import cached_plan, extract_assets, heuristic_plan
from experiments.tools.llm_client import parse_json, text_chat, vlm_chat

__all__ = ["OursFreeformRunner", "run_freeform_loop", "freeform_critique", "freeform_apply"]

_TEXT_MODEL = "Qwen/Qwen3-32B"

_CRITIQUE_SYSTEM = "You are a meticulous academic-poster design critic."
_CRITIQUE_USER = (
    "Look at this rendered academic poster and write a free-form critique of its visual "
    "layout: overlapping text, wasted whitespace, low contrast, figures that are too small, "
    "font sizes, balance, reading flow — whatever you notice. Describe concretely how you "
    "would fix each problem. Write prose, not lists or JSON."
)

_APPLY_SYSTEM = "You output only valid JSON. No prose, no markdown."
_APPLY_USER = """You are an automated poster editor. Given the current poster panels and a free-form
visual critique, produce an edited version that addresses the critique.

Return ONLY a JSON object of this shape:
{{"panels": [{{"section": str, "content": [str, ...], "layout_hint": str, "body_font_scale": float}}, ...],
  "color_theme": str, "global_font_scale": float, "emphasis_level": str}}

Keep the same number and order of panels and their "section" values. You may edit bullet
wording, bullet count, font scales, layout hints, palette. Do NOT add or change figure ids.

CURRENT PANELS + STYLE:
{state}

CRITIQUE:
{critique}
"""


def freeform_critique(image_path: Path, *, model: str = QWEN_VL_MODEL,
                      experiment_logger: Optional[Any] = None) -> str:
    """Free-form (non-schema) VLM critique of a rendered poster image."""
    res = vlm_chat(
        system=_CRITIQUE_SYSTEM,
        user=_CRITIQUE_USER,
        image_paths=[image_path],
        model=model,
        temperature=0.2,
        json_mode=False,  # <-- prose, deliberately NOT constrained to JSON/enums
        experiment_logger=experiment_logger,
        stage_label="freeform_critique",
    )
    return res.get("content", "")


def _state_view(task: PosterTask) -> Dict[str, Any]:
    d = task.model_dump()
    return {
        "panels": [
            {
                "section": p.get("section", ""),
                "content": p.get("content", []),
                "layout_hint": p.get("layout_hint", "text_only"),
                "body_font_scale": p.get("body_font_scale", 1.0),
            }
            for p in d.get("panels", [])
        ],
        "color_theme": d.get("color_theme", "academic_blue"),
        "global_font_scale": d.get("global_font_scale", 1.0),
        "emphasis_level": d.get("emphasis_level", "normal"),
    }


def freeform_apply(task: PosterTask, critique: str, *, model: str = _TEXT_MODEL,
                   experiment_logger: Optional[Any] = None) -> Tuple[PosterTask, bool, str, bool]:
    """LLM best-effort rewrite from a free-form critique.

    Returns ``(new_task, executable, reason, content_changed)``. ``executable`` is
    False whenever the edit can't be parsed/validated/merged or is a no-op — these
    are exactly the free-form failure modes E1 is designed to surface.
    """
    before = task.model_dump()
    user = _APPLY_USER.format(state=json.dumps(_state_view(task), ensure_ascii=False),
                              critique=(critique or "")[:2500])
    try:
        res = text_chat(system=_APPLY_SYSTEM, user=user, model=model, temperature=0.0,
                        json_mode=True, experiment_logger=experiment_logger,
                        stage_label="freeform_apply")
        data = parse_json(res.get("content", ""))
    except Exception as exc:
        return task, False, f"llm_or_json_failed:{type(exc).__name__}", False

    new_panels = data.get("panels")
    if not isinstance(new_panels, list) or len(new_panels) != len(before.get("panels", [])):
        return task, False, "panel_count_mismatch", False

    merged = copy.deepcopy(before)
    content_changed = False
    try:
        for i, np_ in enumerate(new_panels):
            op = merged["panels"][i]
            if isinstance(np_.get("content"), list):
                new_content = [str(b) for b in np_["content"] if str(b).strip()]
                if new_content != op.get("content"):
                    content_changed = True
                op["content"] = new_content
            if "layout_hint" in np_:
                op["layout_hint"] = np_["layout_hint"]
            if "body_font_scale" in np_:
                op["body_font_scale"] = float(np_["body_font_scale"])
            # figure_id deliberately NOT editable (same as SVFP — neither touches binding)
        for k in ("color_theme", "global_font_scale", "emphasis_level"):
            if k in data:
                merged[k] = data[k]
        new_task = PosterTask(**merged)
    except Exception as exc:
        return task, False, f"merge_or_schema_failed:{type(exc).__name__}", False

    if new_task.model_dump() == before:
        return task, False, "no_change", False
    return new_task, True, "ok", content_changed


def run_freeform_loop(task: PosterTask, max_iterations: int, *, work_dir: Path,
                      experiment_logger: Optional[Any] = None) -> Dict[str, Any]:
    """Free-form analogue of VisualFeedbackLoop.run — same render, free-form critique+apply."""
    current = copy.deepcopy(task)
    history: List[Dict[str, Any]] = []
    n_attempts = 0
    n_executed = 0
    content_drift = False
    stop_reason = "max_iterations_reached"

    for it in range(1, max(1, max_iterations) + 1):
        pptx_buf = generate_dashboard_pptx(current)
        pptx_path = work_dir / f"freeform_iter_{it}.pptx"
        pptx_path.write_bytes(pptx_buf.getvalue())
        shot = render_pptx_to_png(pptx_path, work_dir)
        if not shot:
            history.append({"iteration": it, "executable": False, "reason": "no_screenshot"})
            stop_reason = "no_screenshot"
            break

        critique = freeform_critique(Path(shot), experiment_logger=experiment_logger)
        n_attempts += 1
        new_task, ok, reason, changed = freeform_apply(
            current, critique, experiment_logger=experiment_logger)
        content_drift = content_drift or changed
        history.append({
            "iteration": it, "executable": ok, "reason": reason,
            "content_changed": changed, "critique": (critique or "")[:600],
        })
        if ok:
            n_executed += 1
            current = new_task
        else:
            # free-form has no structured "no issues" signal; the honest stop is
            # "the model failed to produce an actionable edit" — a real failure mode.
            stop_reason = f"stuck:{reason}"
            break

    final_buf = generate_dashboard_pptx(current)
    final_path = work_dir / "poster.pptx"
    final_path.write_bytes(final_buf.getvalue())

    return {
        "task": current,
        "history": history,
        "n_iterations": len(history),
        "n_attempts": n_attempts,
        "n_executed": n_executed,
        "action_executability": (n_executed / n_attempts) if n_attempts else 0.0,
        "content_drift": content_drift,
        "convergence_reason": stop_reason,
        "final_path": str(final_path),
    }


class OursFreeformRunner(BaselineRunner):
    name = "ours_freeform"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.max_iterations = int(self.config.get("max_iterations", 4))

    def run(self, paper_path: Path, out_dir: Path, *, timeout_s: int = 1800) -> PosterArtifact:
        cell_dir, log_path, meta, t0 = self._begin(paper_path, out_dir)
        try:
            task = cached_plan(paper_path)
            planner_source = "dify_cache"
            if task is None:
                assets = extract_assets(paper_path)
                meta.paper_title = assets.title
                task = heuristic_plan(assets)
                planner_source = "heuristic"
            else:
                meta.paper_title = task.poster_title

            task.use_commenter = False  # we drive our own free-form loop
            task.max_iterations = self.max_iterations

            result = run_freeform_loop(task, self.max_iterations, work_dir=cell_dir)

            meta.config.update({
                "planner_source": planner_source,
                "feedback_mode": "freeform",
                "action_executability": result["action_executability"],
                "n_executed": result["n_executed"],
                "n_attempts": result["n_attempts"],
                "content_drift": result["content_drift"],
                "convergence_reason": result["convergence_reason"],
            })
            (cell_dir / "freeform_trace.json").write_text(
                json.dumps(result["history"], ensure_ascii=False, indent=2), encoding="utf-8")

            dest_pptx = cell_dir / "poster.pptx"
            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=dest_pptx if dest_pptx.exists() else None,
                panels_json=result["task"].model_dump(),
                log_path=log_path,
            )
        except Exception as exc:
            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=None, panels_json=None, log_path=log_path,
                exit_code=1, error=f"{type(exc).__name__}: {exc}",
            )
