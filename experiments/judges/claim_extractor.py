"""Claim extractor — Qwen2.5-72B atomic-claim extraction from paper PDFs.

A *claim* is a single, self-contained factual statement that the paper
makes (~one short sentence). Used by:

* A1 — to compute information retention rate
* (Future) A3 robustness — cross-check NLI against an LLM-as-judge baseline

Cached at ``experiments/.cache/llm/text/<sha256>.json`` keyed by
SHA256(model + temperature + prompt) so re-runs across baselines for
the same paper hit the cache and we pay just once per paper.

Despite the file name's historical reference to o3, the underlying
model is configurable via the ``model`` kwarg. Default is
Qwen2.5-72B-Instruct via SiliconFlow (matches the rest of the project
and avoids a hard OpenAI dependency).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.pdf_assets import extract_pdf_assets_from_bytes
from experiments.tools.llm_client import parse_json, text_chat


_PROMPT_PATH = Path("experiments/configs/prompts/claim_extraction.txt")


def extract_claims(
    paper_path: Path,
    *,
    model: str = "Qwen/Qwen3-32B",
    temperature: float = 0.0,
    max_claims: int = 60,
    max_paper_chars: int = 24_000,
    experiment_logger: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return a list of atomic claims for ``paper_path``.

    Each claim::

        {"claim_id": "c-001", "text": "We show X improves Y by 13%.", "section": "Results"}
    """
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"prompt template missing: {_PROMPT_PATH}")
    if not paper_path.exists():
        raise FileNotFoundError(f"paper missing: {paper_path}")

    text, _figures = extract_pdf_assets_from_bytes(paper_path.read_bytes())
    user_prompt = (
        _PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{paper_text}", (text or "")[:max_paper_chars])
        .replace("{target_n}", str(max_claims))
    )
    result = text_chat(
        system="You output only valid JSON. Be precise and paper-grounded.",
        user=user_prompt,
        model=model,
        temperature=temperature,
        cache_subdir="claims",
        experiment_logger=experiment_logger,
        stage_label="claim_extraction",
    )
    data = parse_json(result["content"])
    claims_raw = data.get("claims") or []
    claims: List[Dict[str, Any]] = []
    for c in claims_raw[:max_claims]:
        text_val = str(c.get("text") or "").strip()
        if not text_val:
            continue
        claims.append({
            "claim_id": str(c.get("claim_id") or f"c-{len(claims) + 1:03d}"),
            "text": text_val,
            "section": str(c.get("section") or "Other"),
        })
    return claims
