"""NLI judges for A1 and A3.

Two entry points:

* :func:`binary_entailment` — high-level: "is HYPOTHESIS supported by
  PREMISE?" using a single LLM JSON-mode call. Returns ``bool``.
* :func:`bm25_topk_then_nli` — for each bullet, BM25-retrieve the top-K
  most relevant paper sentences then run :func:`three_way_nli` to score
  P(entail) / P(neutral) / P(contradict).

Both pieces use SiliconFlow + Qwen2.5-72B-Instruct by default (overridable
via kwargs). Caching is handled by the shared ``llm_client.text_chat``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from experiments.tools.llm_client import parse_json, text_chat


_NLI_PROMPT_PATH = Path("experiments/configs/prompts/nli_check.txt")
_3WAY_PROMPT = """You are a strict NLI judge. Given PREMISE (a paper sentence) and
HYPOTHESIS (a poster bullet), output the inference relation.

OUTPUT (JSON):
{"label": "entailment" | "neutral" | "contradiction",
 "confidence": <0.0-1.0>,
 "rationale": "<one short sentence>"}

RULES:
- entailment: PREMISE supports HYPOTHESIS (verbatim, paraphrase, or implication).
- contradiction: PREMISE explicitly negates HYPOTHESIS.
- neutral: PREMISE doesn't say either way.
- Numeric claims must match within ±5%; otherwise not entailment.

PREMISE: {premise}
HYPOTHESIS: {hypothesis}
"""


def binary_entailment(
    premise: str,
    hypothesis: str,
    *,
    model: str = "Qwen/Qwen3-32B",
    temperature: float = 0.0,
    experiment_logger: Optional[Any] = None,
) -> bool:
    """Return True iff the LLM judges ``hypothesis`` entailed by ``premise``."""
    if not _NLI_PROMPT_PATH.exists():
        raise FileNotFoundError(f"prompt template missing: {_NLI_PROMPT_PATH}")
    user = (
        _NLI_PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{premise}", (premise or "")[:8000])
        .replace("{hypothesis}", (hypothesis or "")[:2000])
    )
    result = text_chat(
        system="You output only valid JSON. Be precise.",
        user=user,
        model=model,
        temperature=temperature,
        cache_subdir="nli_binary",
        experiment_logger=experiment_logger,
        stage_label="nli_binary",
    )
    try:
        data = parse_json(result["content"])
    except ValueError:
        return False
    return bool(data.get("entailed"))


def three_way_nli(
    premise: str,
    hypothesis: str,
    *,
    model: str = "Qwen/Qwen3-32B",
    temperature: float = 0.0,
    experiment_logger: Optional[Any] = None,
) -> Dict[str, float]:
    """Return ``{"p_entail": ..., "p_neutral": ..., "p_contradict": ...}``.

    The model emits a single label + confidence; we map to a soft
    distribution by routing the confidence to the chosen class and
    splitting the remaining mass uniformly across the other two. Coarse
    but consistent across the dataset.
    """
    user = (
        _3WAY_PROMPT
        .replace("{premise}", (premise or "")[:4000])
        .replace("{hypothesis}", (hypothesis or "")[:2000])
    )
    result = text_chat(
        system="You output only valid JSON.",
        user=user,
        model=model,
        temperature=temperature,
        cache_subdir="nli_3way",
        experiment_logger=experiment_logger,
        stage_label="nli_3way",
    )
    try:
        data = parse_json(result["content"])
    except ValueError:
        return {"p_entail": 0.0, "p_neutral": 1.0, "p_contradict": 0.0}
    label = str(data.get("label") or "neutral").lower()
    conf = float(data.get("confidence") or 0.5)
    conf = max(0.34, min(1.0, conf))  # clamp so the other classes still get mass
    rest = (1.0 - conf) / 2.0
    if label.startswith("entail"):
        return {"p_entail": conf, "p_neutral": rest, "p_contradict": rest}
    if label.startswith("contradict"):
        return {"p_entail": rest, "p_neutral": rest, "p_contradict": conf}
    return {"p_entail": rest, "p_neutral": conf, "p_contradict": rest}


# ---------------------------------------------------------------------------
# BM25 (pure-stdlib implementation) — avoids the rank-bm25 dependency
# ---------------------------------------------------------------------------


_WORD_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


def _tokenise(s: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(s or "")]


def _bm25_scores(query_tokens: List[str], corpus_tokens: List[List[str]], *, k1: float = 1.5, b: float = 0.75) -> List[float]:
    """Standard Okapi BM25. ``corpus_tokens`` is a list of tokenised documents."""
    n_docs = len(corpus_tokens)
    if n_docs == 0:
        return []
    doc_lens = [len(d) for d in corpus_tokens]
    avgdl = sum(doc_lens) / n_docs if n_docs else 1.0

    df: Counter = Counter()
    for d in corpus_tokens:
        for t in set(d):
            df[t] += 1

    scores = [0.0] * n_docs
    for q in set(query_tokens):
        n_q = df.get(q, 0)
        if n_q == 0:
            continue
        idf = math.log((n_docs - n_q + 0.5) / (n_q + 0.5) + 1.0)
        for i, doc in enumerate(corpus_tokens):
            tf = doc.count(q)
            if tf == 0:
                continue
            denom = tf + k1 * (1 - b + b * doc_lens[i] / avgdl)
            scores[i] += idf * tf * (k1 + 1) / denom
    return scores


def _split_into_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?。!?])\s+(?=[A-Z一-鿿])", (text or "").strip())
    return [p.strip() for p in parts if p and len(p.strip()) > 15]


def bm25_topk_then_nli(
    bullets: List[str],
    sentences: List[str],
    *,
    top_k: int = 3,
    model: str = "Qwen/Qwen3-32B",
    experiment_logger: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Per-bullet NLI scores against the BM25-top-K paper sentences.

    Returns one dict per bullet with the aggregated max P(entail) /
    P(contradict) across the K retrieved sentences. ``top_sentences``
    is included so callers can audit the retrieval.
    """
    corpus_tokens = [_tokenise(s) for s in sentences]
    results: List[Dict[str, Any]] = []
    for bullet in bullets:
        q_tokens = _tokenise(bullet)
        scores = _bm25_scores(q_tokens, corpus_tokens)
        ranked = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)[:top_k]
        top_sentences = [sentences[i] for i in ranked if scores[i] > 0]
        if not top_sentences:
            results.append({
                "bullet": bullet,
                "top_sentences": [],
                "p_entail": 0.0,
                "p_neutral": 1.0,
                "p_contradict": 0.0,
            })
            continue
        # Best-of-K: take max P(entail) across retrieved candidates so a
        # single supporting sentence anywhere in the paper rescues the bullet.
        best_entail = 0.0
        best_contradict = 0.0
        best_neutral = 1.0
        for s in top_sentences:
            probs = three_way_nli(s, bullet, model=model, experiment_logger=experiment_logger)
            if probs["p_entail"] > best_entail:
                best_entail = probs["p_entail"]
                best_neutral = probs["p_neutral"]
                best_contradict = probs["p_contradict"]
            best_contradict = max(best_contradict, probs["p_contradict"])
        results.append({
            "bullet": bullet,
            "top_sentences": top_sentences,
            "p_entail": best_entail,
            "p_neutral": best_neutral,
            "p_contradict": best_contradict,
        })
    return results


def split_paper_into_sentences(text: str) -> List[str]:
    """Helper for A3 — exposed so callers don't reimplement the regex."""
    return _split_into_sentences(text)
