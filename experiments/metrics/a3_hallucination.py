"""A3 — Hallucination Rate.

For each poster bullet, retrieve the top-K paper sentences by BM25,
then run a 3-way LLM NLI judge (Qwen3-32B via SiliconFlow, by default).
We classify each bullet by the *winning* NLI label (argmax of the soft
distribution). This fixes a prior bug where neutral / abstained bullets
(p ≈ 0.33 / 0.34 / 0.33) were mislabelled as hallucinations:

    contradicted (= hallucination)  ⇔  P(contradict) is argmax AND ≥ contradict_min
    unsupported  (NOT penalised)    ⇔  neutral wins: paper neither supports nor refutes
    supported                       ⇔  entailment wins

Headline hallucination rate = (# contradicted bullets) / (total bullets);
unsupported_rate is reported separately as a softer signal. Robustness
re-runs with a second model are configurable but disabled by default
for the 5-paper smoke; M4 turns them on at full matrix scale.

NLI heavy lifting lives in ``experiments.judges.nli_judge``; this
metric just selects bullets, aggregates probabilities, and applies
the thresholds.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.pdf_assets import extract_pdf_assets_from_bytes
from experiments.judges.nli_judge import bm25_topk_then_nli, split_paper_into_sentences
from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


@MetricRegistry.register
class A3Hallucination(Metric):
    metric_id = "a3_hallucination"
    description = "Fraction of poster bullets that are not entailed by (and possibly contradicted by) the paper."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if ctx.panels_json is None:
            return self._skip("no panels_json; opaque-baseline path is M3 (OCR + NLI)")
        if not ctx.paper_path.exists():
            return self._skip(f"paper missing: {ctx.paper_path}")

        bullets = _flatten_bullets(ctx.panels_json)
        if not bullets:
            return MetricResult(metric_id=self.metric_id, score=0.0, extra={"n_bullets": 0, "halluc_bullets": 0})
        # Subsample to keep the NLI budget bounded — at top_k=2 and ~28 bullets
        # per poster, 3 baselines × 5 papers ⇒ 840 NLI calls for full coverage.
        # Random fixed-seed sample gives a stable but tractable estimate.
        max_bullets = int(cfg.get("max_bullets_per_poster", 12))
        if len(bullets) > max_bullets:
            import random
            rng = random.Random(42)  # deterministic across cells
            bullets = rng.sample(bullets, max_bullets)

        # Paper sentences for BM25 retrieval
        text, _figures = extract_pdf_assets_from_bytes(ctx.paper_path.read_bytes())
        sentences = split_paper_into_sentences(text)
        if not sentences:
            return self._skip("no sentences extracted from paper")

        # NLI batch
        nli_model = cfg.get("llm_nli", {}).get("model", "Qwen/Qwen3-32B")
        top_k = int(cfg.get("retrieval", {}).get("top_k", 3))
        contradict_min = float(cfg.get("contradict_min", 0.45))

        results = bm25_topk_then_nli(bullets, sentences, top_k=top_k, model=nli_model)

        contradicted: List[Dict[str, Any]] = []
        unsupported: List[Dict[str, Any]] = []
        supported: List[Dict[str, Any]] = []
        for r in results:
            label = _classify_nli_result(r, contradict_min=contradict_min)
            if label == "contradicted":
                contradicted.append(r)   # paper explicitly refutes the bullet → hallucination
            elif label == "supported":
                supported.append(r)      # paper entails the bullet
            else:
                unsupported.append(r)    # neutral: paper is silent — NOT a hallucination

        n = max(1, len(results))
        # Headline hallucination = contradicted only (semantically correct; ↓ better)
        rate = len(contradicted) / n
        return MetricResult(
            metric_id=self.metric_id,
            score=rate,
            extra={
                "n_bullets": len(results),
                "contradicted_bullets": len(contradicted),
                "unsupported_bullets": len(unsupported),
                "supported_bullets": len(supported),
                "contradicted_rate": round(len(contradicted) / n, 4),
                "unsupported_rate": round(len(unsupported) / n, 4),
                "supported_rate": round(len(supported) / n, 4),
                "contradict_min": contradict_min,
                "examples_contradicted": [
                    {"bullet": r["bullet"][:140], "p_entail": round(r["p_entail"], 3),
                     "p_neutral": round(r["p_neutral"], 3), "p_contradict": round(r["p_contradict"], 3)}
                    for r in contradicted[:5]
                ],
                "examples_unsupported": [
                    {"bullet": r["bullet"][:140], "p_entail": round(r["p_entail"], 3),
                     "p_neutral": round(r["p_neutral"], 3), "p_contradict": round(r["p_contradict"], 3)}
                    for r in unsupported[:5]
                ],
            },
        )


def _flatten_bullets(panels_json: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for p in (panels_json or {}).get("panels", []) or []:
        for b in p.get("content", []) or []:
            s = (b or "").strip()
            if s:
                out.append(s)
    return out


def _classify_nli_result(result: Dict[str, Any], *, contradict_min: float) -> str:
    """Return supported/unsupported/contradicted for one 3-way NLI result.

    The important rule is that neutral/abstained distributions are not counted
    as hallucinations. A bullet is contradicted only when contradiction is the
    winning label and clears the configured threshold.
    """
    pe = float(result["p_entail"])
    pn = float(result["p_neutral"])
    pc = float(result["p_contradict"])
    if pc >= pe and pc >= pn and pc >= contradict_min:
        return "contradicted"
    if pe >= pn and pe >= pc:
        return "supported"
    return "unsupported"
