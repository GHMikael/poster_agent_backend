"""End-to-end smoke for the M2 acceptance gate without requiring a real PDF.

Patches :func:`experiments.baselines._planner_shared.extract_assets` with
a stub returning a minimal :class:`PaperAssets`, runs the
:class:`OursNoSVFPRunner`, then asserts that ``poster.pptx`` and
``metadata.json`` land in the cell folder and that ``compute_metrics``
produces D1/D3 outputs.

Skipped when LibreOffice is missing (would still produce a pptx but no
png, and the test stays useful as a check that the runner doesn't crash).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from app.models import ExtractedFigure
from experiments.baselines import _planner_shared
from experiments.baselines import ours_no_svfp as ours_no_svfp_mod
from experiments.baselines.ours_no_svfp import OursNoSVFPRunner
from experiments.tools.experiment_logger import JsonlExperimentLogger


class EndToEndSmokeTest(unittest.TestCase):
    """Runs the OursNoSVFP runner with a stubbed planner and checks
    the artifact layout downstream metrics depend on."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name) / "artifacts"
        self.out_dir.mkdir()
        # Synthetic PDF path; not actually read because we stub extract_assets.
        self.fake_pdf = Path(self._tmp.name) / "2099.00001.pdf"
        self.fake_pdf.write_bytes(b"%PDF-1.4\n%fake\n")

        # Stub assets — deterministic content so D1 latency is comparable
        # across CI runs.
        def _stub_extract(paper_path: Path) -> _planner_shared.PaperAssets:
            return _planner_shared.PaperAssets(
                text=(
                    "EduIllustrate proposes a benchmark for K-12 STEM content. "
                    "We introduce a four-stage protocol with sequential anchoring. "
                    "Method evaluates ten LLMs on text-diagram explanation. "
                    "Results show Gemini 3.0 Pro Preview leading at 87.8%. "
                    "Conclusion notes future work on dynamic content generation."
                ),
                figures={},  # no figures to keep the test fast and deterministic
                title="Smoke Paper",
                authors="A. Tester",
            )

        self._saved_extract = ours_no_svfp_mod.extract_assets
        ours_no_svfp_mod.extract_assets = _stub_extract

        # Also reset experiment env so the runner installs its own.
        self._saved_env = {k: os.environ.get(k) for k in ("POSTER_EXPERIMENT_MODE", "POSTER_EXPERIMENT_LOG")}

    def tearDown(self) -> None:
        ours_no_svfp_mod.extract_assets = self._saved_extract
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_ours_no_svfp_produces_artifact_with_metadata(self) -> None:
        runner = OursNoSVFPRunner(config={})
        artifact = runner.run(self.fake_pdf, self.out_dir, timeout_s=60)

        # Layout downstream metrics depend on:
        cell_dir = self.out_dir / f"ours_no_svfp_{self.fake_pdf.stem}"
        self.assertTrue(cell_dir.exists(), f"missing cell dir: {cell_dir}")
        self.assertTrue((cell_dir / "metadata.json").exists())
        self.assertTrue(artifact.pptx_path.exists(), f"missing pptx: {artifact.pptx_path}")
        self.assertGreater(artifact.pptx_path.stat().st_size, 5_000, "pptx unexpectedly tiny")

        meta = json.loads((cell_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["baseline"], "ours_no_svfp")
        self.assertEqual(meta["arxiv_id"], self.fake_pdf.stem)
        self.assertEqual(meta["exit_code"], 0, f"runner failed: {meta.get('error')}")
        self.assertGreater(meta["total_latency_ms"], 0)


class MetricsSmokeTest(unittest.TestCase):
    """Verifies D1/D2/D3 can compute from a minimal cell folder."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cell = Path(self._tmp.name) / "ours_no_svfp_smoke"
        self.cell.mkdir()
        # A fake but non-empty pptx file (≥ 1 KB) so D3 doesn't trip the
        # empty-pptx fail rule.
        (self.cell / "poster.pptx").write_bytes(b"PK" + b"X" * 4096)
        # metadata.json
        (self.cell / "metadata.json").write_text(
            json.dumps(
                {
                    "baseline": "ours_no_svfp",
                    "arxiv_id": "smoke",
                    "total_latency_ms": 1234.5,
                    "exit_code": 0,
                    "error": "",
                    "started_at": "2026-05-23T03:00:00+00:00",
                    "finished_at": "2026-05-23T03:00:02+00:00",
                    "config": {},
                }
            ),
            encoding="utf-8",
        )
        # experiment_log.jsonl with one stage + one llm_call so D1/D2 have data.
        logger = JsonlExperimentLogger(log_path=self.cell / "experiment_log.jsonl", run_id="smoke")
        logger.log_stage(stage="pptx_gen", latency_ms=900.0)
        logger.log_stage(stage="run_total", latency_ms=1234.5)
        logger.log_llm_call(
            stage="vlm_layout_review",
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            prompt_tokens=1500,
            completion_tokens=300,
            latency_ms=789.0,
        )

    def test_d1_d2_d3_compute_from_cell(self) -> None:
        from experiments.metrics.base import MetricContext, MetricRegistry
        import experiments.metrics.d1_latency  # noqa: F401  registers
        import experiments.metrics.d2_cost  # noqa: F401
        import experiments.metrics.d3_failure_rate  # noqa: F401

        ctx = MetricContext(
            artifact_dir=self.cell,
            pptx_path=self.cell / "poster.pptx",
            png_path=None,
            panels_json=None,
            experiment_log_path=self.cell / "experiment_log.jsonl",
            paper_path=Path("/no/such.pdf"),
            paper_meta={},
            config={},
        )

        d1 = MetricRegistry.get("d1_latency")().compute(ctx)
        d2 = MetricRegistry.get("d2_cost")().compute(ctx)
        d3 = MetricRegistry.get("d3_failure_rate")().compute(ctx)

        self.assertFalse(d1.skipped)
        # run_total dominates so D1 score == 1234.5
        self.assertAlmostEqual(d1.score, 1234.5, places=1)
        self.assertIn("pptx_gen", d1.extra["per_stage_ms"])

        self.assertFalse(d2.skipped)
        self.assertGreater(d2.score, 0.0)
        self.assertIn("Qwen/Qwen2.5-VL-72B-Instruct", d2.extra["per_model"])

        self.assertEqual(d3.score, 0.0)  # no failure
        self.assertEqual(d3.extra["reasons"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
