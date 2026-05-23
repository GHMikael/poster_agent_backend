"""LLM-based zero-shot poster planner used by ``gpt4o_zeroshot`` baseline.

Despite the historical name, this planner is **provider-agnostic**: it
takes any OpenAI-compatible ``(api_key, base_url, model)`` tuple. The
default uses SiliconFlow + Qwen2.5-72B-Instruct (matches the rest of
the project), but a config tweak switches it to true GPT-4o when an
``OPENAI_API_KEY`` is available.

Why this design choice: the rest of the project authenticates against
SiliconFlow via ``DASHSCOPE_API_KEY``. Forcing a hard OpenAI dependency
would block reviewers without an OpenAI key from reproducing the
baseline. The paper should describe this baseline as "single text-LLM
zero-shot" and cite the specific model id used.

Caches per SHA256(paper_text[:8000] + model + temperature + prompt_text).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from openai import OpenAI

from app.models import FigureAsset, Panel, PosterLayout, PosterTask
from experiments.baselines._planner_shared import PaperAssets


# Match app/config.py — make DASHSCOPE_API_KEY / OPENAI_API_KEY visible
# to subprocess-spawned baseline runners that never imported app.config.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


_PROMPT_PATH = Path("experiments/configs/prompts/gpt4o_zeroshot_plan.txt")
_CACHE_DIR = Path("experiments/.cache/gpt4o_planner")

# Default to SiliconFlow + Qwen2.5-72B-Instruct (matches project config).
# Override at call time when an OpenAI key is available.
_DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
_DEFAULT_MODEL = "Qwen/Qwen3-32B"
_DEFAULT_API_KEY_ENV = "DASHSCOPE_API_KEY"


def plan_poster(
    assets: PaperAssets,
    *,
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.2,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key_env: str = _DEFAULT_API_KEY_ENV,
    experiment_logger: Optional[Any] = None,
    max_paper_chars: int = 12_000,
) -> PosterTask:
    """Single LLM call: paper text + figure list → PosterTask JSON.

    Errors raise ``ValueError`` (the baseline runner records exit_code=1).
    """
    prompt_template = _read_prompt()
    figure_list = _serialise_figure_list(assets.figures)
    user_prompt = (
        prompt_template
        .replace("{title}", assets.title or "(unknown title)")
        .replace("{paper_text}", (assets.text or "")[:max_paper_chars])
        .replace("{figure_list}", figure_list)
    )

    cache_key = _cache_key(user_prompt, model, temperature)
    cached = _load_cache(cache_key)
    if cached is not None:
        data = cached
        cache_hit = True
        latency_ms = 0.0
        prompt_tokens = completion_tokens = 0
    else:
        cache_hit = False
        client = OpenAI(
            api_key=api_key or os.getenv(api_key_env, ""),
            base_url=base_url or _DEFAULT_BASE_URL,
        )
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You output only valid JSON."},
                {"role": "user", "content": user_prompt},
            ],
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        content = (resp.choices[0].message.content or "").strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"planner did not return valid JSON: {exc}\nfirst 400 chars: {content[:400]!r}") from exc

        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        _save_cache(cache_key, data)

    if experiment_logger is not None:
        try:
            experiment_logger.log_llm_call(
                stage="gpt4o_zeroshot_plan",
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                raw_response=None,
                retries=0,
                extra={"cache_hit": cache_hit},
            )
        except Exception:
            pass

    return _build_poster_task(data, assets)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_prompt() -> str:
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"prompt template missing: {_PROMPT_PATH}")
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _serialise_figure_list(figures: Dict[str, Any]) -> str:
    if not figures:
        return "(no figures extracted)"
    lines = []
    for fid, fig in figures.items():
        caption = (getattr(fig, "caption", "") or "")[:160]
        lines.append(f"- id={fid}  caption={caption!r}")
    return "\n".join(lines)


def _cache_key(prompt: str, model: str, temperature: float) -> str:
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8"))
    h.update(model.encode("utf-8"))
    h.update(f"{temperature:.4f}".encode("utf-8"))
    return h.hexdigest()[:24]


def _load_cache(key: str) -> Optional[Dict[str, Any]]:
    path = _CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(key: str, data: Dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_CACHE_DIR / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_poster_task(data: Dict[str, Any], assets: PaperAssets) -> PosterTask:
    """Validate the LLM JSON against PosterTask. Falls back gracefully on
    missing optional fields so a partially-conformant response still
    yields a renderable poster (helps reviewers without re-running)."""
    panels_raw = data.get("panels") or []
    if not isinstance(panels_raw, list) or len(panels_raw) == 0:
        raise ValueError("planner output has no panels")

    figures_by_id: Dict[str, FigureAsset] = {}
    for fid, fig in (assets.figures or {}).items():
        figures_by_id[fid] = FigureAsset(
            caption=getattr(fig, "caption", "") or "",
            type="other",
            description=(getattr(fig, "caption", "") or "")[:120],
            importance="medium",
            image_source=getattr(fig, "image_source", "") or "",
            image_url=getattr(fig, "image_url", "") or "",
            thumbnail_url=getattr(fig, "thumbnail_url", "") or "",
        )

    panels: list = []
    for p in panels_raw:
        section = str(p.get("section") or "Section")
        content = [str(b)[:200] for b in (p.get("content") or []) if str(b).strip()]
        if not content:
            content = ["(no content extracted)"]
        fig_id = str(p.get("figure_id") or "")
        layout_hint = str(p.get("layout_hint") or "text_only")
        figure_caption = str(p.get("figure_caption") or "")
        if fig_id and fig_id not in figures_by_id:
            # LLM hallucinated a figure id — drop the reference rather than fail.
            fig_id = ""
            layout_hint = "text_only"
        panels.append(Panel(
            section=section,
            content=content,
            figure_id=fig_id,
            figure_caption=figure_caption,
            layout_hint=layout_hint,
            body_font_scale=float(p.get("body_font_scale") or 1.0),
        ))

    return PosterTask(
        asset_token=f"gpt4o_zeroshot_{hashlib.sha1((assets.title or '').encode()).hexdigest()[:10]}",
        template=str(data.get("template") or "template_dashboard"),
        layout_variant=str(data.get("layout_variant") or "auto"),
        color_theme=str(data.get("color_theme") or "academic_blue"),
        emphasis_level=str(data.get("emphasis_level") or "normal"),
        poster_title=str(data.get("poster_title") or assets.title or "Untitled"),
        authors=str(data.get("authors") or assets.authors or ""),
        paper_info=str(data.get("paper_info") or ""),
        layout=PosterLayout(),
        panels=panels,
        figures=figures_by_id,
        use_commenter=False,
        max_iterations=1,
    )
