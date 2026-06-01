from app.models import FigureAsset, Panel, PosterTask
from app.ppt_renderer import bind_available_figures


def test_bind_available_figures_attaches_method_and_results_assets():
    task = PosterTask(
        poster_title="Paper",
        panels=[
            Panel(section="Motivation", content=["why"]),
            Panel(section="Method", content=["how"]),
            Panel(section="Results", content=["score"]),
        ],
        figures={
            "FigA": FigureAsset(
                caption="Model architecture overview",
                type="architecture",
                best_matched_section="Method",
                importance="high",
                audit_status="ok",
                image_source="/tmp/fake_a.png",
            ),
            "FigB": FigureAsset(
                caption="Evaluation results",
                type="table",
                best_matched_section="Results",
                importance="medium",
                audit_status="ok",
                image_source="/tmp/fake_b.png",
            ),
        },
    )

    bind_available_figures(task)

    by_section = {panel.section: panel for panel in task.panels}
    assert by_section["Method"].figure_id == "FigA"
    assert by_section["Results"].figure_id == "FigB"
    assert by_section["Method"].layout_hint == "text_left_image_right"


def test_bind_available_figures_does_not_overwrite_existing_binding():
    task = PosterTask(
        poster_title="Paper",
        panels=[Panel(section="Method", content=["how"], figure_id="Existing")],
        figures={
            "Existing": FigureAsset(image_source="/tmp/existing.png"),
            "FigA": FigureAsset(
                caption="Model architecture overview",
                best_matched_section="Method",
                importance="high",
                audit_status="ok",
                image_source="/tmp/fake_a.png",
            ),
        },
    )

    bind_available_figures(task)

    assert task.panels[0].figure_id == "Existing"


def test_bind_available_figures_requires_ok_audit_status():
    task = PosterTask(
        poster_title="Paper",
        panels=[Panel(section="Method", content=["how"])],
        figures={
            "FigA": FigureAsset(
                caption="Model architecture overview",
                best_matched_section="Method",
                importance="high",
                image_source="/tmp/fake_a.png",
            ),
        },
    )

    bind_available_figures(task)

    assert task.panels[0].figure_id == ""
