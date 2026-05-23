"""A3 — Hallucination Rate.

For each poster bullet, retrieve the top-K paper sentences by BM25,
then run an LLM NLI judge (Qwen3-32B via SiliconFlow, by default):

    bullet is hallucinated  ⇔  P(entail) < entail_min  AND  P(contradict) > contradict_max

The rate is (# hallucinated bullets) / (total bullets). Robustness
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
        entail_min = float(cfg.get("entail_min", 0.4))
        contradict_max = float(cfg.get("contradict_max", 0.3))

        results = bm25_topk_then_nli(bullets, sentences, top_k=top_k, model=nli_model)

        halluc: List[Dict[str, Any]] = []
        for r in results:
            if r["p_entail"] < entail_min and r["p_contradict"] > contradict_max:
                halluc.append(r)

        rate = len(halluc) / max(1, len(bullets))
        return MetricResult(
            metric_id=self.metric_id,
            score=rate,
            extra={
                "n_bullets": len(bullets),
                "halluc_bullets": len(halluc),
                "thresholds": {"entail_min": entail_min, "contradict_max": contradict_max},
                "examples": [
                    {"bullet": r["bullet"][:140], "p_entail": round(r["p_entail"], 3),
                     "p_contradict": round(r["p_contradict"], 3)}
                    for r in halluc[:5]
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
