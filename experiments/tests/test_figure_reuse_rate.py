from pathlib import Path

from experiments.metrics.base import MetricContext, MetricRegistry
import experiments.metrics.figure_reuse_rate  # noqa: F401


def _ctx(tmp_path: Path, panels_json: dict) -> MetricContext:
    cell = tmp_path / "ours_svfp_paper"
    cell.mkdir()
    return MetricContext(
        artifact_dir=cell,
        pptx_path=cell / "poster.pptx",
        png_path=None,
        panels_json=panels_json,
        experiment_log_path=None,
        paper_path=Path("/no/such.pdf"),
        paper_meta={},
        config={},
    )


def test_figure_reuse_rate_counts_valid_reused_figures(tmp_path):
    fig1 = tmp_path / "fig1.png"
    fig2 = tmp_path / "fig2.png"
    fig1.write_bytes(b"fake")
    fig2.write_bytes(b"fake")
    ctx = _ctx(
        tmp_path,
        {
            "figures": {
                "Fig1": {"image_source": str(fig1)},
                "Fig2": {"image_source": str(fig2)},
                "Fig3": {"image_source": str(tmp_path / "missing.png")},
            },
            "panels": [
                {"section": "Method", "figure_id": "Fig1"},
                {"section": "Results", "figure_id": "Fig3"},
            ],
        },
    )

    result = MetricRegistry.get("figure_reuse_rate")().compute(ctx)

    assert result.score == 0.5
    assert result.extra["reused_valid_figure_ids"] == ["Fig1"]
    assert result.extra["missing_or_invalid_references"] == ["Fig3"]


def test_figure_reuse_rate_filters_unsafe_audit_status(tmp_path):
    fig1 = tmp_path / "fig1.png"
    fig1.write_bytes(b"fake")
    ctx = _ctx(
        tmp_path,
        {
            "figures": {
                "Fig1": {"image_source": str(fig1), "audit_status": "broken"},
            },
            "panels": [{"section": "Method", "figure_id": "Fig1"}],
        },
    )

    result = MetricRegistry.get("figure_reuse_rate")().compute(ctx)

    assert result.skipped
    assert result.skip_reason == "no valid source figures"


def test_figure_reuse_rate_resolves_direct_figure_source(tmp_path):
    fig1 = tmp_path / "fig1.png"
    fig1.write_bytes(b"fake")
    ctx = _ctx(
        tmp_path,
        {
            "figures": {
                "Fig1": {"image_source": str(fig1)},
            },
            "panels": [
                {"section": "Method", "figure": str(fig1), "figure_id": ""},
            ],
        },
    )

    result = MetricRegistry.get("figure_reuse_rate")().compute(ctx)

    assert result.score == 1.0
    assert result.extra["reused_valid_figure_ids"] == ["Fig1"]
