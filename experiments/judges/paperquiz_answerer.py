"""PaperQuiz answerer — a VLM reads the poster PNG and picks answers.

Each call goes to one VLM (Qwen-VL by default). Answers are scored
against the MCQ's ``correct`` field.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from experiments.tools.llm_client import parse_json, vlm_chat


_ANSWER_PROMPT = """Answer these multiple-choice questions using ONLY the poster image.
Do not use external knowledge. If the poster does not state the answer,
pick the option closest to what the poster says.

OUTPUT (JSON only):
{"answers": [{"question_id": "q-001", "choice": "A"}, {"question_id": "q-002", "choice": "C"}, ...]}

QUESTIONS:
{questions_block}
"""


def answer_mcqs(
    *,
    png_path: Path,
    mcqs: List[Dict[str, Any]],
    answerer_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Returns one dict per MCQ::

        {"question_id": "q-001", "predicted": "B", "correct": True, "raw": "B"}
    """
    if not mcqs:
        return []
    questions_block = _format_questions(mcqs)
    user = _ANSWER_PROMPT.replace("{questions_block}", questions_block)
    result = vlm_chat(
        system="You output only valid JSON.",
        user=user,
        image_paths=[png_path],
        model=answerer_cfg.get("model", "Qwen/Qwen3-VL-32B-Instruct"),
        temperature=float(answerer_cfg.get("temperature", 0.0)),
        stage_label=f"paperquiz_answer:{answerer_cfg.get('id', 'vlm')}",
    )
    try:
        data = parse_json(result["content"])
    except ValueError:
        return [{"question_id": m["question_id"], "predicted": "", "correct": False, "raw": ""} for m in mcqs]

    by_qid: Dict[str, str] = {}
    for a in data.get("answers", []) or []:
        qid = str(a.get("question_id", ""))
        choice = str(a.get("choice", "")).strip().upper()
        if choice in {"A", "B", "C", "D"}:
            by_qid[qid] = choice

    out: List[Dict[str, Any]] = []
    for m in mcqs:
        predicted = by_qid.get(m["question_id"], "")
        out.append({
            "question_id": m["question_id"],
            "predicted": predicted,
            "correct": predicted == m["correct"],
            "raw": predicted,
        })
    return out


def _format_questions(mcqs: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for m in mcqs:
        lines.append(f"[{m['question_id']}] {m['question']}")
        for k, v in m["options"].items():
            lines.append(f"  {k}. {v}")
    return "\n".join(lines)
