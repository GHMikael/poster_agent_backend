import json
from pathlib import Path

from experiments.metrics.base import MetricContext, MetricRegistry
import experiments.metrics.protocol_metrics  # noqa: F401


def _ctx(tmp_path: Path, config: dict) -> MetricContext:
    cell = tmp_path / "ours_svfp_smoke"
    cell.mkdir()
    (cell / "metadata.json").write_text(
        json.dumps({"config": config}, ensure_ascii=False),
        encoding="utf-8",
    )
    return MetricContext(
        artifact_dir=cell,
        pptx_path=cell / "poster.pptx",
        png_path=None,
        panels_json=None,
        experiment_log_path=None,
        paper_path=Path("/no/such.pdf"),
        paper_meta={},
        config={},
    )


def test_protocol_metrics_read_baseline_metadata(tmp_path):
    ctx = _ctx(
        tmp_path,
        {
            "feedback_mode": "svfp_closed_set",
            "action_executability": 1.0,
            "converged": True,
            "n_iterations": 2,
            "per_iter_visual_gain": 0.35,
        },
    )

    assert MetricRegistry.get("action_executability")().compute(ctx).score == 1.0
    assert MetricRegistry.get("convergence_rate")().compute(ctx).score == 1.0
    assert MetricRegistry.get("mean_iters_to_converge")().compute(ctx).score == 2.0
    assert MetricRegistry.get("per_iter_visual_gain")().compute(ctx).score == 0.35


def test_protocol_metrics_skip_no_feedback_arm(tmp_path):
    ctx = _ctx(tmp_path, {"feedback_mode": "none", "n_iterations": 0})

    result = MetricRegistry.get("convergence_rate")().compute(ctx)

    assert result.skipped
    assert result.skip_reason == "no feedback arm"


def test_convergence_rate_treats_iteration_budget_as_non_converged(tmp_path):
    ctx = _ctx(
        tmp_path,
        {
            "feedback_mode": "svfp_closed_set",
            "converged": True,
            "convergence_reason": "max_iterations_reached",
        },
    )

    assert MetricRegistry.get("convergence_rate")().compute(ctx).score == 0.0
