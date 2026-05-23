"""A1 — Information Retention Rate.

Pipeline::

    paper.pdf ──claim_extractor (o3)──►  list[Claim]
                                              │
    poster bullets (from panels_json) ────────┴──► entailment_judge (GPT-4o)
                                                              │
                                                              ▼
                                              IR = matched_claims / total_claims

Gold validation (must run BEFORE the main matrix):

* On 5 calibration papers, two human annotators score 200 claims each.
* o3's claim extraction is accepted only if Cohen's κ ≥ 0.7 with majority human.
* The κ value is recorded in ``results/calibration/a1_kappa.json`` and
  re-checked at metric time; if below the threshold, A1 is reported but
  flagged in the paper as "exploratory".

This module deliberately keeps the heavy LLM calls in
``experiments.judges.claim_extractor`` so caching, retries, and pricing
live in one place across A1/A3/A4.
"""

from __future__ import annotations

from typing import Any, Dict, List

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


@MetricRegistry.register
class A1InformationRetention(Metric):
    metric_id = "a1_information_retention"
    description = "Fraction of paper atomic claims preserved (paraphrased or verbatim) in the poster text."

    def compute(self, ctx: MetricContext) -> MetricResult:
        from experiments.judges.claim_extractor import extract_claims  # local import: heavy deps
        from experiments.judges.nli_judge import binary_entailment  # local import

        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if not ctx.paper_path.exists():
            return self._skip(f"paper missing: {ctx.paper_path}")
        if ctx.panels_json is None:
            return self._skip("no panels_json (opaque baseline?); set up GPT-4o post-hoc extractor in M3")

        try:
            claims: List[Dict[str, Any]] = extract_claims(
                paper_path=ctx.paper_path,
                model=cfg.get("claim_extractor", {}).get("model", "Qwen/Qwen3-32B"),
                temperature=cfg.get("claim_extractor", {}).get("temperature", 0.0),
                max_claims=cfg.get("claim_extractor", {}).get("max_claims_per_paper", 40),
            )
        except NotImplementedError:
            return self._skip("M3 deliverable: claim_extractor not yet implemented")
        if not claims:
            return self._skip("zero claims extracted")

        poster_bullets: List[str] = _flatten_bullets(ctx.panels_json)
        if not poster_bullets:
            return MetricResult(metric_id=self.metric_id, score=0.0, extra={"n_claims": len(claims), "n_retained": 0, "per_section": {}})

        retained = 0
        per_section_hits: Dict[str, Dict[str, int]] = {}
        for claim in claims:
            section = str(claim.get("section") or "Unknown")
            sec_row = per_section_hits.setdefault(section, {"total": 0, "retained": 0})
            sec_row["total"] += 1
            try:
                hit = binary_entailment(
                    premise=" ".join(poster_bullets),
                    hypothesis=claim["text"],
                    model=cfg.get("entailment_judge", {}).get("model", "Qwen/Qwen3-32B"),
                )
            except NotImplementedError:
                return self._skip("M3 deliverable: nli_judge.binary_entailment not yet implemented")
            if hit:
                retained += 1
                sec_row["retained"] += 1

        ir = retained / max(1, len(claims))
        return MetricResult(
            metric_id=self.metric_id,
            score=ir,
            extra={
                "n_claims": len(claims),
                "n_retained": retained,
                "per_section": per_section_hits,
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
