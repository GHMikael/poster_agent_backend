"""PaperQuiz MCQ generator — LLM produces 5 questions per paper.

Designed so the **same** 5 MCQs are used across all baselines for a
given paper (questions are paper-level, not baseline-level). The cache
key is SHA256(paper_text + generator_config) so re-runs cost nothing.

Self-consistency filter (light version — full multi-round filter is M4):

1. Generate N MCQs.
2. Ask the generator to answer them given the paper text only (no poster).
3. Keep MCQs where the model's own answer matches ``correct``.
4. If fewer than ``min_kept`` survive, re-generate once with temperature
   bumped to 0.4 to diversify the question set, then merge.
5. Persist the surviving set as the test set for all answerers.

Default model is Qwen3-32B via SiliconFlow (matches the project setup).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.pdf_assets import extract_pdf_assets_from_bytes
from experiments.tools.llm_client import parse_json, text_chat


_PROMPT_PATH = Path("experiments/configs/prompts/paperquiz_generation.txt")
_CACHE_ROOT = Path("experiments/.cache/paperquiz")


def get_or_generate_mcqs(
    paper_path: Path,
    *,
    cache_dir: Path = _CACHE_ROOT,
    gen_cfg: Dict[str, Any] | None = None,
    experiment_logger: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Returns a list of MCQs (or [] if generation fails self-consistency).

    Cached per (paper, generator_config) so all baselines for the same
    paper see identical questions.
    """
    cfg = gen_cfg or {}
    n_questions = int(cfg.get("n_questions", 5))
    model = cfg.get("model", "Qwen/Qwen3-32B")
    max_paper_chars = int(cfg.get("max_paper_chars", 16_000))
    min_kept = int(cfg.get("min_kept_self_consistency", max(3, n_questions - 1)))

    paper_text, _figures = extract_pdf_assets_from_bytes(paper_path.read_bytes())
    paper_text = (paper_text or "")[:max_paper_chars]

    key_src = f"{paper_path.stem}|{model}|n={n_questions}|chars={max_paper_chars}|{hashlib.sha256(paper_text.encode()).hexdigest()[:16]}"
    cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:24]
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"prompt template missing: {_PROMPT_PATH}")

    mcqs = _generate_pool(paper_text, n_questions=n_questions, model=model, temperature=0.2,
                          experiment_logger=experiment_logger)
    kept = _self_consistency_filter(mcqs, paper_text=paper_text, model=model,
                                    experiment_logger=experiment_logger)
    if len(kept) < min_kept:
        extra_mcqs = _generate_pool(paper_text, n_questions=n_questions, model=model, temperature=0.4,
                                     experiment_logger=experiment_logger)
        extra_kept = _self_consistency_filter(extra_mcqs, paper_text=paper_text, model=model,
                                              experiment_logger=experiment_logger)
        seen = {m["question"] for m in kept}
        for m in extra_kept:
            if m["question"] not in seen and len(kept) < n_questions:
                kept.append(m)

    if not kept:
        return []

    # Renumber so question_ids are stable + sequential after filtering
    for i, m in enumerate(kept, start=1):
        m["question_id"] = f"q-{i:03d}"

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return kept


def _generate_pool(
    paper_text: str,
    *,
    n_questions: int,
    model: str,
    temperature: float,
    experiment_logger: Optional[Any],
) -> List[Dict[str, Any]]:
    user = (
        _PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{paper_text}", paper_text)
        .replace("{n_questions}", str(n_questions))
    )
    result = text_chat(
        system="You output only valid JSON.",
        user=user,
        model=model,
        temperature=temperature,
        cache_subdir="paperquiz_gen",
        experiment_logger=experiment_logger,
        stage_label="paperquiz_generation",
    )
    try:
        data = parse_json(result["content"])
    except ValueError:
        return []
    raw = data.get("mcqs") or []
    out: List[Dict[str, Any]] = []
    for m in raw:
        opts = m.get("options") or {}
        correct = str(m.get("correct") or "").strip().upper()
        if correct not in opts:
            continue
        q = str(m.get("question") or "").strip()
        if not q:
            continue
        out.append({
            "question_id": str(m.get("question_id") or f"q-{len(out) + 1:03d}"),
            "question": q,
            "options": {k: str(v) for k, v in opts.items() if k in {"A", "B", "C", "D"}},
            "correct": correct,
            "section": str(m.get("section") or "Other"),
        })
    return out


def _self_consistency_filter(
    mcqs: List[Dict[str, Any]],
    *,
    paper_text: str,
    model: str,
    experiment_logger: Optional[Any],
) -> List[Dict[str, Any]]:
    """Drop MCQs where the same model fails to answer correctly from paper text alone."""
    if not mcqs:
        return []
    prompt = (
        "Answer these multiple-choice questions using ONLY the paper text below. "
        "Output JSON: {\"answers\": [{\"question_id\": \"q-001\", \"choice\": \"A\"}, ...]}\n\n"
        "PAPER TEXT:\n" + paper_text + "\n\n"
        "QUESTIONS:\n" + _format_mcqs_for_prompt(mcqs)
    )
    result = text_chat(
        system="You output only valid JSON.",
        user=prompt,
        model=model,
        temperature=0.0,
        cache_subdir="paperquiz_selfcheck",
        experiment_logger=experiment_logger,
        stage_label="paperquiz_self_consistency",
    )
    try:
        data = parse_json(result["content"])
    except ValueError:
        return mcqs  # be lenient if self-check JSON breaks
    by_qid = {str(a.get("question_id")): str(a.get("choice", "")).upper() for a in data.get("answers", []) or []}
    kept: List[Dict[str, Any]] = []
    for m in mcqs:
        predicted = by_qid.get(m["question_id"], "")
        if predicted == m["correct"]:
            kept.append(m)
    return kept


def _format_mcqs_for_prompt(mcqs: List[Dict[str, Any]]) -> str:
    lines = []
    for m in mcqs:
        lines.append(f"[{m['question_id']}] {m['question']}")
        for k, v in m["options"].items():
            lines.append(f"  {k}. {v}")
    return "\n".join(lines)
