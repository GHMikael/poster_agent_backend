"""A4 — Section Coverage.

A canonical section S ∈ {Motivation, Problem, Method, Experiments,
Results, Conclusion} is "covered" iff:

* any panel's ``section`` field matches S via Levenshtein-similarity ≥ τ
  (default 0.8), OR
* a GPT-4o binary judge says some panel's bullets are *about* S.

Score = covered / 6.

For baselines without panel ``section`` labels (Paper2Poster /
PosterAgent), only the LLM-judge path is used over OCR'd panel text
(M3 deliverable).
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


@MetricRegistry.register
class A4SectionCoverage(Metric):
    metric_id = "a4_section_coverage"
    description = "Fraction of canonical sections (Motivation/Problem/Method/Experiments/Results/Conclusion) covered."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if ctx.panels_json is None:
            return self._skip("no panels_json; OCR fallback for opaque baselines is M3")

        canonical: List[str] = cfg.get("canonical_sections") or [
            "Motivation", "Problem", "Method", "Experiments", "Results", "Conclusion",
        ]
        threshold = float(cfg.get("levenshtein_min", 0.8))
        panel_sections = [str(p.get("section") or "") for p in (ctx.panels_json or {}).get("panels", [])]

        covered: Dict[str, bool] = {}
        for sec in canonical:
            covered[sec] = any(_similarity(sec, ps) >= threshold for ps in panel_sections)

        # M3: optional LLM-judge for the ones still uncovered.
        # for sec, ok in covered.items():
        #     if not ok:
        #         covered[sec] = experiments.judges.section_judge.is_about(panel_bullets, sec)

        n = sum(1 for v in covered.values() if v)
        return MetricResult(
            metric_id=self.metric_id,
            score=n / max(1, len(canonical)),
            extra={"per_section": covered, "n_covered": n, "n_total": len(canonical)},
        )


def _similarity(a: str, b: str) -> float:
    """Normalised Levenshtein similarity in [0,1]. Pure stdlib (no
    python-Levenshtein dependency for the M2 gate)."""
    a = unicodedata.normalize("NFKC", a or "").lower().strip()
    b = unicodedata.normalize("NFKC", b or "").lower().strip()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Quick containment short-circuit
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    # Standard O(mn) Levenshtein
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    distance = prev[n]
    return 1.0 - distance / max(m, n)
