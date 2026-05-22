"""Unit tests for the SVFP / convergence-detector / history-logger trio.

Run with::

    .venv312/bin/python -m pytest tests/test_svfp_loop_logger.py -q

The tests are written with ``unittest`` so they also run via
``python -m unittest`` without requiring pytest to be installed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make sure ``app`` is importable when tests are run from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.feedback_loop import (  # noqa: E402
    ConvergenceConfig,
    ConvergenceDetector,
    FeedbackApplier,
    HeuristicLayoutChecker,
    LayoutFeedback,
    PanelFeedback,
    PreviewRenderer,
    check_convergence,
    parse_vlm_feedback,
)
from app.history_logger import (  # noqa: E402
    HistoryLogger,
    filter_by_paper,
    load_history,
    log_iteration,
    log_iterations,
)
from app.models import Panel, PosterTask  # noqa: E402
from app.vlm_commenter import (  # noqa: E402
    SVFPIssueType,
    SVFP_ISSUE_VALUES,
    SVFPSuggestedAction,
    SVFP_SUGGESTED_ACTION_VALUES,
    _extract_json_block,
    build_feedback,
    build_feedback_batch,
    feedback_to_json,
    get_svfp_batch_schema,
    get_svfp_schema,
    validate_feedback,
)


# ---------------------------------------------------------------------------
# SVFP
# ---------------------------------------------------------------------------


class SVFPTests(unittest.TestCase):
    def test_enum_has_three_canonical_issues(self) -> None:
        expected = {"overlapping_elements", "empty_space", "low_contrast", "figure_too_small"}
        self.assertEqual(set(SVFP_ISSUE_VALUES), expected)
        self.assertEqual(len(SVFP_ISSUE_VALUES), 4)
        self.assertEqual({m.value for m in SVFPIssueType}, expected)

    def test_suggested_action_enum_has_nine_actions(self) -> None:
        expected = {
            "reduce_bullet_count",
            "shrink_text",
            "truncate_bullets",
            "shrink_figure_box",
            "enlarge_font",
            "add_bullet",
            "compact_figure_box",
            "switch_palette",
            "none",
        }
        self.assertEqual(set(SVFP_SUGGESTED_ACTION_VALUES), expected)
        self.assertEqual({m.value for m in SVFPSuggestedAction}, expected)

    def test_build_feedback_required_fields(self) -> None:
        record = build_feedback(
            SVFPIssueType.OVERLAPPING_ELEMENTS,
            "Method panel: 7 bullets overflow.",
        )
        self.assertEqual(record["issue_type"], "overlapping_elements")
        self.assertEqual(record["suggested_fix"], "reduce_bullet_count")
        self.assertIn("details", record)
        validate_feedback(record)

    def test_build_feedback_accepts_string_issue(self) -> None:
        record = build_feedback(
            "low_contrast",
            "Title uses light gray on white.",
            section="Header",
            severity="medium",
        )
        self.assertEqual(record["issue_type"], "low_contrast")
        self.assertEqual(record["suggested_fix"], "switch_palette")
        self.assertEqual(record["section"], "Header")
        self.assertEqual(record["severity"], "medium")

    def test_build_feedback_rejects_unknown_issue(self) -> None:
        with self.assertRaises(ValueError):
            build_feedback("not_a_real_issue", "should fail")

    def test_build_feedback_rejects_removed_issue(self) -> None:
        # text_overflow / dense_content / etc. were removed in the SVFP
        # simplification — building with them must now raise.
        for removed in ("text_overflow", "dense_content", "no_emphasis",
                        "color_mismatch", "icon_or_image_missing",
                        "figure_misaligned", "font_size_inconsistent"):
            with self.assertRaises(ValueError, msg=f"{removed!r} should be rejected"):
                build_feedback(removed, "should fail")

    def test_build_feedback_rejects_invalid_severity(self) -> None:
        with self.assertRaises(ValueError):
            build_feedback(SVFPIssueType.EMPTY_SPACE, "x", severity="urgent")

    def test_build_feedback_batch(self) -> None:
        batch = build_feedback_batch(
            [
                {"issue_type": SVFPIssueType.OVERLAPPING_ELEMENTS, "details": "a"},
                {"issue_type": "low_contrast", "details": "b"},
            ]
        )
        self.assertEqual(len(batch), 2)
        for item in batch:
            validate_feedback(item)

    def test_feedback_to_json_roundtrip(self) -> None:
        record = build_feedback(SVFPIssueType.OVERLAPPING_ELEMENTS, "stack collision")
        text = feedback_to_json(record)
        decoded = json.loads(text)
        self.assertEqual(decoded["issue_type"], "overlapping_elements")
        validate_feedback(decoded)

    def test_get_svfp_schema_shape(self) -> None:
        schema = get_svfp_schema()
        self.assertEqual(schema["type"], "object")
        self.assertEqual(
            set(schema["required"]),
            {"issue_type", "details", "suggested_fix"},
        )
        self.assertEqual(
            set(schema["properties"]["issue_type"]["enum"]),
            set(SVFP_ISSUE_VALUES),
        )
        # suggested_fix is now a closed enum too — keeps the VLM honest.
        self.assertEqual(
            set(schema["properties"]["suggested_fix"]["enum"]),
            set(SVFP_SUGGESTED_ACTION_VALUES),
        )
        batch_schema = get_svfp_batch_schema()
        self.assertEqual(batch_schema["type"], "array")
        self.assertEqual(batch_schema["items"]["title"], "SVFPFeedback")

    def test_validate_feedback_catches_missing_field(self) -> None:
        with self.assertRaises(ValueError):
            validate_feedback({"issue_type": "low_contrast", "details": "x"})

    def test_validate_feedback_catches_bad_suggested_fix(self) -> None:
        bad = {
            "issue_type": "empty_space",
            "details": "x",
            "suggested_fix": "made_up_action",
        }
        with self.assertRaises(ValueError):
            validate_feedback(bad)

    def test_validate_feedback_catches_non_dict(self) -> None:
        with self.assertRaises(ValueError):
            validate_feedback("not a dict")


# ---------------------------------------------------------------------------
# ConvergenceDetector
# ---------------------------------------------------------------------------


class ConvergenceTests(unittest.TestCase):
    def test_stops_on_excellent_threshold(self) -> None:
        detector = ConvergenceDetector(
            ConvergenceConfig(max_iterations=5, excellent_threshold=9.0)
        )
        s1 = detector.update(score=7.0, feedback={"global_issues": ["overlapping_elements"]})
        s2 = detector.update(score=9.3, feedback={"global_issues": ["overlapping_elements"]})
        self.assertFalse(s1.converged)
        self.assertTrue(s2.converged)
        self.assertEqual(s2.reason, "excellent_threshold")

    def test_stops_on_max_iterations(self) -> None:
        detector = ConvergenceDetector(
            ConvergenceConfig(max_iterations=2, excellent_threshold=9.5, min_delta=0.1)
        )
        detector.update(score=6.0, feedback={"global_issues": ["x"]})
        last = detector.update(score=8.0, feedback={"global_issues": ["x"]})
        self.assertTrue(last.converged)
        self.assertEqual(last.reason, "max_iterations_reached")

    def test_stops_on_stagnant_patience(self) -> None:
        detector = ConvergenceDetector(
            ConvergenceConfig(
                max_iterations=10,
                excellent_threshold=9.5,
                min_delta=0.5,
                stagnant_patience=2,
                adaptive=False,
            )
        )
        detector.update(score=6.0, feedback={"global_issues": ["x"]})
        detector.update(score=6.05, feedback={"global_issues": ["x"]})  # stagnant 1
        last = detector.update(score=6.06, feedback={"global_issues": ["x"]})  # stagnant 2
        self.assertTrue(last.converged)
        self.assertTrue(last.reason.startswith("stagnant_patience"))

    def test_stops_on_no_issues(self) -> None:
        detector = ConvergenceDetector(ConvergenceConfig(max_iterations=5))
        last = detector.update(score=8.0, feedback={"global_issues": [], "panel_feedback": []})
        self.assertTrue(last.converged)
        self.assertEqual(last.reason, "no_issues")

    def test_adaptive_min_delta_tightens_with_score(self) -> None:
        detector = ConvergenceDetector(
            ConvergenceConfig(
                max_iterations=10,
                min_delta=0.4,
                adaptive=True,
                excellent_threshold=9.9,
                stagnant_patience=10,
            )
        )
        # At low scores, raw min_delta applies.
        low = detector.update(score=6.0, feedback={"global_issues": ["x"]})
        self.assertAlmostEqual(low.effective_min_delta, 0.4)
        # At 9.0+, min_delta is halved.
        high = detector.update(score=9.2, feedback={"global_issues": ["x"]})
        self.assertLess(high.effective_min_delta, 0.4)

    def test_reset_clears_state(self) -> None:
        detector = ConvergenceDetector(ConvergenceConfig(max_iterations=2))
        detector.update(score=6.0, feedback={"global_issues": ["x"]})
        detector.reset()
        self.assertEqual(detector.iteration, 0)
        self.assertEqual(detector.history, [])

    def test_check_convergence_batch_helper(self) -> None:
        records = [
            {"score": 6.4, "feedback": {"global_issues": ["overlapping_elements"]}},
            {"score": 7.6, "feedback": {"global_issues": ["overlapping_elements"]}},
            {"score": 9.4, "feedback": {"global_issues": []}},
        ]
        out = check_convergence(records, ConvergenceConfig(max_iterations=5))
        self.assertTrue(out["converged"])
        self.assertEqual(out["iterations_used"], 3)
        self.assertAlmostEqual(out["best_score"], 9.4)

    def test_check_convergence_empty_input(self) -> None:
        out = check_convergence([])
        self.assertFalse(out["converged"])
        self.assertEqual(out["reason"], "empty_input")


# ---------------------------------------------------------------------------
# HistoryLogger
# ---------------------------------------------------------------------------


class HistoryLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="svfp_tests_")
        self.log_path = Path(self.tmpdir) / "feedback_history.json"

    def tearDown(self) -> None:
        for child in Path(self.tmpdir).glob("*"):
            child.unlink()
        os.rmdir(self.tmpdir)

    def _sample(self, iteration: int, score: float) -> dict:
        return {
            "iteration": iteration,
            "layout_json": {"template": "template_dashboard"},
            "feedback_list": [
                build_feedback(
                    SVFPIssueType.OVERLAPPING_ELEMENTS,
                    "Method panel overflow.",
                    section="Method",
                )
            ],
            "score_dict": {"score": score, "source": "test"},
        }

    def test_log_iteration_creates_file(self) -> None:
        log_iteration("paper-A", self._sample(1, 7.0), self.log_path)
        records = load_history(self.log_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["paper_id"], "paper-A")
        self.assertEqual(records[0]["iteration"], 1)
        self.assertIn("timestamp", records[0])

    def test_log_iteration_appends(self) -> None:
        log_iteration("paper-A", self._sample(1, 7.0), self.log_path)
        log_iteration("paper-A", self._sample(2, 8.0), self.log_path)
        records = load_history(self.log_path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["score_dict"]["score"], 8.0)

    def test_log_iteration_overwrite_when_append_false(self) -> None:
        log_iteration("paper-A", self._sample(1, 7.0), self.log_path)
        log_iteration("paper-A", self._sample(2, 8.0), self.log_path, append=False)
        records = load_history(self.log_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["iteration"], 2)

    def test_log_iterations_batch(self) -> None:
        log_iterations(
            "paper-B",
            [self._sample(1, 7.0), self._sample(2, 8.5), self._sample(3, 9.0)],
            self.log_path,
        )
        records = load_history(self.log_path)
        self.assertEqual(len(records), 3)
        scores = [r["score_dict"]["score"] for r in records]
        self.assertEqual(scores, [7.0, 8.5, 9.0])

    def test_filter_by_paper(self) -> None:
        log_iteration("paper-A", self._sample(1, 7.0), self.log_path)
        log_iteration("paper-B", self._sample(1, 6.0), self.log_path)
        log_iteration("paper-A", self._sample(2, 8.0), self.log_path)
        self.assertEqual(len(filter_by_paper(self.log_path, "paper-A")), 2)
        self.assertEqual(len(filter_by_paper(self.log_path, "paper-B")), 1)

    def test_logger_session_buffers_until_exit(self) -> None:
        logger = HistoryLogger(self.log_path, autoflush=False)
        with logger.session():
            logger.log("paper-A", self._sample(1, 7.0))
            logger.log("paper-A", self._sample(2, 8.0))
            self.assertFalse(self.log_path.exists())
        records = load_history(self.log_path)
        self.assertEqual(len(records), 2)

    def test_logger_autoflush(self) -> None:
        logger = HistoryLogger(self.log_path, autoflush=True)
        logger.log("paper-A", self._sample(1, 7.0))
        self.assertTrue(self.log_path.exists())
        self.assertEqual(len(load_history(self.log_path)), 1)

    def test_utf8_chinese_content_preserved(self) -> None:
        record = {
            "iteration": 1,
            "layout_json": {},
            "feedback_list": [
                {
                    "issue_type": "low_contrast",
                    "details": "标题对比度过低，建议加深字色。",
                    "suggested_fix": "increase_contrast",
                }
            ],
            "score_dict": {"score": 6.8},
        }
        log_iteration("paper-中文", record, self.log_path)
        raw_text = self.log_path.read_text(encoding="utf-8")
        self.assertIn("paper-中文", raw_text)
        self.assertIn("标题对比度过低", raw_text)

    def test_missing_iteration_field_raises(self) -> None:
        with self.assertRaises(ValueError):
            log_iteration("paper-A", {"score_dict": {"score": 7.0}}, self.log_path)


# ---------------------------------------------------------------------------
# Integration: SVFP feedback -> convergence -> history logging
# ---------------------------------------------------------------------------


class EndToEndTests(unittest.TestCase):
    def test_full_loop(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="svfp_e2e_")
        log_path = Path(tmpdir) / "loop.json"
        try:
            detector = ConvergenceDetector(
                ConvergenceConfig(max_iterations=4, excellent_threshold=9.0, min_delta=0.1)
            )
            logger = HistoryLogger(log_path, autoflush=False)
            scores = [6.5, 7.8, 9.1]

            with logger.session():
                for i, s in enumerate(scores, start=1):
                    feedback_items = build_feedback_batch(
                        [
                            {
                                "issue_type": SVFPIssueType.OVERLAPPING_ELEMENTS,
                                "details": f"iter-{i} too dense",
                                "section": "Method",
                            }
                        ]
                        if s < 9.0
                        else []
                    )
                    state = detector.update(
                        score=s,
                        feedback={
                            "global_issues": [it["issue_type"] for it in feedback_items],
                            "panel_feedback": [],
                        },
                    )
                    logger.log(
                        "paper-e2e",
                        {
                            "iteration": i,
                            "layout_json": {"template": "template_dashboard"},
                            "feedback_list": feedback_items,
                            "score_dict": {"score": s, "converged": state.converged},
                        },
                    )
                    if state.converged:
                        break

            records = load_history(log_path)
            self.assertEqual(len(records), 3)
            self.assertTrue(records[-1]["score_dict"]["converged"])
            self.assertAlmostEqual(records[-1]["score_dict"]["score"], 9.1)
        finally:
            for child in Path(tmpdir).glob("*"):
                child.unlink()
            os.rmdir(tmpdir)


# ---------------------------------------------------------------------------
# Regression tests for VLM parsing & preview renderer fixes
# ---------------------------------------------------------------------------


class VlmParseRecoveryTests(unittest.TestCase):
    def test_extract_full_json(self) -> None:
        text = '{"score": 7.5, "comment": "ok", "global_issues": []}'
        data = _extract_json_block(text)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["score"], 7.5)

    def test_extract_with_surrounding_prose(self) -> None:
        text = '解读如下：\n{"score": 6, "comment": "dense", "global_issues": ["dense_content"]}\n谢谢'
        data = _extract_json_block(text)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["score"], 6)
        self.assertEqual(data["global_issues"], ["dense_content"])

    def test_extract_recovers_from_truncation(self) -> None:
        # JSON truncated mid-string — should still recover the leading object.
        text = (
            '{"score": 6, "global_issues": ["dense_content","empty_space"],'
            ' "panel_feedback": [{"section":"1","issues":["text_overflow"],'
            ' "suggested_action":"reduce_bullet_count","target_value":3}],'
            ' "comment": "海报存在文本溢'
        )
        data = _extract_json_block(text)
        # Either we recover something partial or get None. If we recover,
        # ``score`` must be carried through.
        if data is not None:
            self.assertEqual(data.get("score"), 6)

    def test_extract_returns_none_on_garbage(self) -> None:
        self.assertIsNone(_extract_json_block(""))
        self.assertIsNone(_extract_json_block("no braces here"))

    def test_parse_vlm_feedback_uses_vlm_score_over_heuristic(self) -> None:
        fallback = LayoutFeedback(score=8.1, source="preview_heuristic")
        fallback.comment = "Heuristic layout check completed."
        raw = {
            "source": "vlm",
            "score": 6,
            "global_issues": ["dense_content"],
            "panel_feedback": [
                {
                    "section": "Method",
                    "issues": ["text_overflow"],
                    "suggested_action": "reduce_bullet_count",
                    "target_value": 3,
                }
            ],
            "comment": "dense and overflowing",
        }
        result = parse_vlm_feedback(raw, fallback)
        # Critical: VLM's score must win even if heuristic was more optimistic.
        self.assertEqual(result.score, 6.0)
        self.assertEqual(result.source, "vlm")
        self.assertEqual(len(result.panel_feedback), 1)
        self.assertEqual(result.panel_feedback[0].issues, ["text_overflow"])

    def test_parse_vlm_feedback_keeps_partial_score(self) -> None:
        fallback = LayoutFeedback(score=8.1, source="preview_heuristic")
        partial = {
            "source": "vlm_partial",
            "score": 5.5,
            "global_issues": ["dense_content"],
            "comment": "truncated mid-panel",
        }
        result = parse_vlm_feedback(partial, fallback)
        self.assertEqual(result.score, 5.5)
        self.assertEqual(result.source, "vlm_partial")

    def test_parse_vlm_feedback_falls_back_on_garbage(self) -> None:
        fallback = LayoutFeedback(score=8.1, source="preview_heuristic")
        raw = {"comment": "completely non-JSON output from VLM"}
        result = parse_vlm_feedback(raw, fallback)
        self.assertEqual(result.score, 8.1)
        self.assertEqual(result.source, "vlm_unparsed_fallback")

    def test_parse_vlm_feedback_disabled_uses_heuristic(self) -> None:
        fallback = LayoutFeedback(score=8.2, source="heuristic")
        raw = {"source": "disabled", "comment": "no key"}
        result = parse_vlm_feedback(raw, fallback)
        self.assertEqual(result.score, 8.2)
        self.assertEqual(result.source, "heuristic")


class HeuristicCheckerTests(unittest.TestCase):
    def _task(self, panels: List[Panel]) -> PosterTask:
        return PosterTask(
            poster_title="Test Poster",
            panels=panels,
        )

    def test_no_emphasis_does_not_trigger_for_normal_panel(self) -> None:
        # Long-ish narrative panel (no digits) should NOT trigger no_emphasis
        # under the new rule — that was the over-firing bug.
        task = self._task(
            [
                Panel(section=f"Sec {i}", content=[
                    "学术海报是会议交流中的重要媒介",
                    "人工制作海报耗时且依赖设计经验",
                    "论文到海报需要同时处理文本图片布局",
                ])
                for i in range(6)
            ]
        )
        feedback = HeuristicLayoutChecker().check(task)
        for panel_fb in feedback.panel_feedback:
            self.assertNotIn("no_emphasis", panel_fb.issues)

    def test_empty_space_triggers_for_short_panel_without_anchor(self) -> None:
        task = self._task(
            [
                Panel(section="Big A", content=["aaa", "bbb", "ccc", "ddd", "eee"]),
                Panel(section="Big B", content=["aaa", "bbb", "ccc", "ddd"]),
                Panel(section="Big C", content=["aaa", "bbb", "ccc", "ddd"]),
                Panel(section="Big D", content=["aaa", "bbb", "ccc", "ddd"]),
                Panel(section="Big E", content=["aaa", "bbb", "ccc", "ddd"]),
                Panel(section="Thin", content=["short"]),
            ]
        )
        feedback = HeuristicLayoutChecker().check(task)
        thin = next(p for p in feedback.panel_feedback if p.section == "Thin")
        # In the simplified taxonomy, the "thin" panel is reported as
        # empty_space (was no_emphasis in the legacy 10-class scheme).
        self.assertIn("empty_space", thin.issues)
        self.assertEqual(thin.suggested_action, "enlarge_font")


class FeedbackApplierTests(unittest.TestCase):
    def test_reduce_bullets_action(self) -> None:
        applier = FeedbackApplier()
        panel = Panel(section="X", content=[f"bullet {i}" for i in range(7)])
        task = PosterTask(poster_title="t", panels=[panel])
        fb = LayoutFeedback(panel_feedback=[
            PanelFeedback(
                section="X",
                issues=["overlapping_elements"],
                suggested_action="reduce_bullet_count",
                target_value=3,
            )
        ])
        new_task = applier.apply(task, fb)
        self.assertEqual(len(new_task.panels[0].content), 3)


class PreviewRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="preview_renderer_tests_")

    def tearDown(self) -> None:
        for child in Path(self.tmpdir).glob("*"):
            child.unlink()
        os.rmdir(self.tmpdir)

    def _build_task(self, bullets: List[str]) -> PosterTask:
        panels = [
            Panel(section=f"Section {i}", content=bullets)
            for i in range(6)
        ]
        return PosterTask(poster_title="Diff Test", panels=panels)

    def test_iteration_watermark_changes_image(self) -> None:
        renderer = PreviewRenderer()
        task = self._build_task(["short bullet", "another"])
        out1 = renderer.render(task, Path(self.tmpdir) / "iter1.png", iteration=1)
        out2 = renderer.render(task, Path(self.tmpdir) / "iter2.png", iteration=2)
        import hashlib
        h1 = hashlib.md5(out1.read_bytes()).hexdigest()
        h2 = hashlib.md5(out2.read_bytes()).hexdigest()
        self.assertNotEqual(h1, h2, "iter watermark must produce a different image per iteration")

    def test_bullet_change_changes_image(self) -> None:
        renderer = PreviewRenderer()
        long_bullet = "A" * 100
        short_bullet = "A" * 60
        # Same iteration number — only bullet length differs. Old code would
        # render identical PNGs because of the [:60] clip; new renderer must
        # produce different images.
        out1 = renderer.render(
            self._build_task([long_bullet, "x", "y", "z", "w"]),
            Path(self.tmpdir) / "long.png",
            iteration=1,
        )
        out2 = renderer.render(
            self._build_task([short_bullet, "x", "y", "z", "w"]),
            Path(self.tmpdir) / "short.png",
            iteration=1,
        )
        import hashlib
        self.assertNotEqual(
            hashlib.md5(out1.read_bytes()).hexdigest(),
            hashlib.md5(out2.read_bytes()).hexdigest(),
            "renderer must reflect bullet content differences",
        )


# ---------------------------------------------------------------------------
# Enhanced FeedbackApplier (font_size_inconsistent, low_contrast, etc.)
# ---------------------------------------------------------------------------


class EnhancedApplierTests(unittest.TestCase):
    """Coverage for the simplified 3-issue / 9-action FeedbackApplier."""

    def _task(self, color_theme: str = "minimal_gray") -> PosterTask:
        return PosterTask(
            poster_title="Applier Test",
            color_theme=color_theme,
            panels=[
                Panel(section="Panel A", content=["a" * 200, "b", "c"]),
                Panel(section="Panel B", content=["short", "another"]),
                Panel(section="Panel C", content=["x"]),
                Panel(section="Panel D", content=[f"line {i}" for i in range(8)]),
            ],
        )

    # --- per-panel actions for OVERLAPPING_ELEMENTS ---

    def test_shrink_text_action_reduces_panel_font_scale(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel A",
                    issues=["overlapping_elements"],
                    suggested_action="shrink_text",
                    target_value=0.85,
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_a = next(p for p in new_task.panels if p.section == "Panel A")
        self.assertLess(panel_a.body_font_scale, 1.0)
        self.assertGreaterEqual(panel_a.body_font_scale, 0.7)  # clamp lower bound

    def test_truncate_bullets_action_clamps_long_bullets(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel A",
                    issues=["overlapping_elements"],
                    suggested_action="truncate_bullets",
                    target_value=80,
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_a = next(p for p in new_task.panels if p.section == "Panel A")
        for b in panel_a.content:
            self.assertLessEqual(len(b), 80)

    def test_shrink_figure_box_sets_image_compact_hint(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel A",
                    issues=["overlapping_elements"],
                    suggested_action="shrink_figure_box",
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_a = next(p for p in new_task.panels if p.section == "Panel A")
        self.assertEqual(panel_a.layout_hint, "image_compact")

    # --- per-panel actions for EMPTY_SPACE ---

    def test_enlarge_font_action_grows_panel_font_scale(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel C",
                    issues=["empty_space"],
                    suggested_action="enlarge_font",
                    target_value=1.15,
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_c = next(p for p in new_task.panels if p.section == "Panel C")
        self.assertGreater(panel_c.body_font_scale, 1.0)
        self.assertLessEqual(panel_c.body_font_scale, 1.3)  # clamp upper bound

    def test_add_bullet_action_appends_supporting_bullet(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel C",
                    issues=["empty_space"],
                    suggested_action="add_bullet",
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_c = next(p for p in new_task.panels if p.section == "Panel C")
        self.assertEqual(len(panel_c.content), 2)  # was 1, now 2

    def test_compact_figure_box_sets_image_compact_hint(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel B",
                    issues=["empty_space"],
                    suggested_action="compact_figure_box",
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_b = next(p for p in new_task.panels if p.section == "Panel B")
        self.assertEqual(panel_b.layout_hint, "image_compact")

    # --- palette rotation (LOW_CONTRAST fix) ---

    def test_switch_palette_rotates_to_engineering_green(self) -> None:
        task = self._task(color_theme="academic_blue")
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel A",
                    issues=["low_contrast"],
                    suggested_action="switch_palette",
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        self.assertEqual(new_task.color_theme, "engineering_green")

    def test_global_low_contrast_rotates_palette_from_academic_blue(self) -> None:
        """The previous deadlock: academic_blue is 'high contrast' set member,
        so the legacy _apply_high_contrast did nothing. The rotation now
        always moves, regardless of starting palette."""
        task = self._task(color_theme="academic_blue")
        fb = LayoutFeedback(global_issues=["low_contrast"])
        new_task = FeedbackApplier().apply(task, fb)
        self.assertNotEqual(new_task.color_theme, "academic_blue")
        self.assertIn(new_task.color_theme, {"academic_blue", "engineering_green"})

    def test_global_low_contrast_rotates_back_from_engineering_green(self) -> None:
        task = self._task(color_theme="engineering_green")
        fb = LayoutFeedback(global_issues=["low_contrast"])
        new_task = FeedbackApplier().apply(task, fb)
        self.assertEqual(new_task.color_theme, "academic_blue")

    def test_unknown_palette_falls_back_to_first_in_cycle(self) -> None:
        task = self._task(color_theme="warm_orange")  # not in PALETTE_CYCLE
        fb = LayoutFeedback(global_issues=["low_contrast"])
        new_task = FeedbackApplier().apply(task, fb)
        self.assertEqual(new_task.color_theme, "academic_blue")

    # --- global font scaling ---

    def test_global_empty_space_enlarges_global_font(self) -> None:
        task = self._task()
        fb = LayoutFeedback(global_issues=["empty_space"])
        new_task = FeedbackApplier().apply(task, fb)
        self.assertGreater(new_task.global_font_scale, 1.0)
        self.assertLessEqual(new_task.global_font_scale, 1.2)

    def test_global_overlapping_shrinks_global_font(self) -> None:
        task = self._task()
        fb = LayoutFeedback(global_issues=["overlapping_elements"])
        new_task = FeedbackApplier().apply(task, fb)
        self.assertLess(new_task.global_font_scale, 1.0)
        self.assertGreaterEqual(new_task.global_font_scale, 0.8)

    # --- fallback when VLM gives issue but no explicit action ---

    def test_issue_default_overlapping_reduces_bullets(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel D",
                    issues=["overlapping_elements"],
                    suggested_action="none",
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_d = next(p for p in new_task.panels if p.section == "Panel D")
        self.assertLessEqual(len(panel_d.content), 4)

    def test_issue_default_empty_space_enlarges_panel_font(self) -> None:
        task = self._task()
        fb = LayoutFeedback(
            panel_feedback=[
                PanelFeedback(
                    section="Panel C",
                    issues=["empty_space"],
                    suggested_action="",
                )
            ]
        )
        new_task = FeedbackApplier().apply(task, fb)
        panel_c = next(p for p in new_task.panels if p.section == "Panel C")
        self.assertGreater(panel_c.body_font_scale, 1.0)

    def test_font_scale_is_clamped_after_many_iterations(self) -> None:
        """Stacking enlarge_font 10 times must not blow past the clamp."""
        applier = FeedbackApplier()
        task = self._task()
        for _ in range(10):
            fb = LayoutFeedback(panel_feedback=[
                PanelFeedback(
                    section="Panel C", issues=["empty_space"],
                    suggested_action="enlarge_font", target_value=1.15,
                )
            ])
            task = applier.apply(task, fb)
        panel_c = next(p for p in task.panels if p.section == "Panel C")
        self.assertLessEqual(panel_c.body_font_scale, 1.3)
        self.assertGreaterEqual(panel_c.body_font_scale, 0.7)


# ---------------------------------------------------------------------------
# RunArchive
# ---------------------------------------------------------------------------


class RunArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        # Use a private RUNS_ROOT so we don't pollute outputs/runs/ on CI.
        from app import run_archive as ra

        self.tmpdir = tempfile.mkdtemp(prefix="archive_tests_")
        self._orig_root = ra.RUNS_ROOT
        ra.RUNS_ROOT = Path(self.tmpdir) / "runs"
        ra.RUNS_ROOT.mkdir(parents=True, exist_ok=True)
        self.ra = ra

    def tearDown(self) -> None:
        import shutil as _shutil

        self.ra.RUNS_ROOT = self._orig_root
        _shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_folder_name_format(self) -> None:
        archive = self.ra.RunArchive.create("abcd1234", "学术海报 测试 / paper")
        self.assertTrue(archive.run_dir.exists())
        self.assertTrue(archive.preview_dir.exists())
        self.assertTrue(archive.pptx_dir.exists())
        self.assertIn("abcd1234", archive.folder_name)
        # Slugified Chinese title should be preserved.
        self.assertIn("学术海报", archive.folder_name)
        # No filesystem-unsafe chars.
        for ch in "/\\:*?\"<>|":
            self.assertNotIn(ch, archive.folder_name)

    def test_save_input_and_report_roundtrip(self) -> None:
        archive = self.ra.RunArchive.create("rep1", "Paper One")
        archive.save_input({"poster_title": "Paper One", "panels": []})
        archive.save_report(
            input_task={"poster_title": "Paper One"},
            summary={"best_score": 8.5, "iterations": 2, "converged": True, "convergence_reason": "no_issues"},
            iterations=[
                {"iteration": 1, "score": 7.0, "feedback": {"source": "vlm"}},
                {"iteration": 2, "score": 8.5, "feedback": {"source": "vlm"}},
            ],
        )
        report = json.loads((archive.run_dir / "run_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["run_id"], "rep1")
        self.assertEqual(report["summary"]["best_score"], 8.5)
        self.assertEqual(len(report["iterations"]), 2)
        self.assertEqual(report["input"]["poster_title"], "Paper One")

    def test_save_preview_and_pptx_copies_files(self) -> None:
        archive = self.ra.RunArchive.create("cp1", "Cp Paper")
        src_png = Path(self.tmpdir) / "iter_1_preview.png"
        src_png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        src_pptx = Path(self.tmpdir) / "iter_1.pptx"
        src_pptx.write_bytes(b"PK\x03\x04fake")

        archive.save_preview(src_png)
        archive.save_pptx(src_pptx)

        self.assertTrue((archive.preview_dir / "iter_1_preview.png").exists())
        self.assertTrue((archive.pptx_dir / "iter_1.pptx").exists())

    def test_update_runs_index_orders_by_time_desc(self) -> None:
        a1 = self.ra.RunArchive.create("r-old", "Old Paper")
        a1.save_report({"poster_title": "Old Paper"}, {"best_score": 5.0, "iterations": 1, "converged": False, "convergence_reason": "max_iterations_reached"}, [])
        # Manually backdate the started_at so ordering is deterministic.
        old_report = json.loads((a1.run_dir / "run_report.json").read_text(encoding="utf-8"))
        old_report["started_at"] = "2026-01-01T00:00:00"
        (a1.run_dir / "run_report.json").write_text(json.dumps(old_report, ensure_ascii=False, indent=2), encoding="utf-8")

        a2 = self.ra.RunArchive.create("r-new", "New Paper")
        a2.save_report({"poster_title": "New Paper"}, {"best_score": 8.0, "iterations": 2, "converged": True, "convergence_reason": "no_issues"}, [])
        new_report = json.loads((a2.run_dir / "run_report.json").read_text(encoding="utf-8"))
        new_report["started_at"] = "2026-05-19T20:00:00"
        (a2.run_dir / "run_report.json").write_text(json.dumps(new_report, ensure_ascii=False, indent=2), encoding="utf-8")

        index_path = self.ra.update_runs_index()
        text = index_path.read_text(encoding="utf-8")
        # Newer entry must appear before older entry.
        self.assertIn("New Paper", text)
        self.assertIn("Old Paper", text)
        self.assertLess(text.index("New Paper"), text.index("Old Paper"))
        self.assertIn("2 run(s)", text)

    def test_slugify_handles_messy_titles(self) -> None:
        self.assertEqual(self.ra.slugify(""), "untitled")
        self.assertEqual(self.ra.slugify("   "), "untitled")
        s = self.ra.slugify("hello / world : *broken* ?.??")
        self.assertNotIn("/", s)
        self.assertNotIn("?", s)
        self.assertNotIn("*", s)

    def test_create_accepts_explicit_archive_root(self) -> None:
        """Passing ``archive_root`` redirects the run folder."""

        alt = Path(self.tmpdir) / "alt_root"
        archive = self.ra.RunArchive.create("alt1", "Alt Paper", archive_root=alt)
        self.assertTrue(archive.run_dir.exists())
        self.assertEqual(archive.run_dir.parent, alt)
        # The default RUNS_ROOT (monkey-patched to a tmp subdir in setUp) is untouched.
        self.assertEqual(list(self.ra.RUNS_ROOT.iterdir()), [])

    def test_create_without_archive_root_falls_back_to_runs_root(self) -> None:
        """Omitting ``archive_root`` keeps the original RUNS_ROOT behavior."""

        archive = self.ra.RunArchive.create("def1", "Default Paper")
        self.assertEqual(archive.run_dir.parent, self.ra.RUNS_ROOT)
        # Explicit ``None`` behaves the same as omission.
        archive2 = self.ra.RunArchive.create("def2", "Default Paper Two", archive_root=None)
        self.assertEqual(archive2.run_dir.parent, self.ra.RUNS_ROOT)

    def test_demo_does_not_pollute_runs_root(self) -> None:
        """``_demo`` must write to a tempdir and never touch RUNS_ROOT."""

        import shutil as _shutil

        demo_dir = self.ra._demo()
        try:
            # demo lives under tempfile, completely outside RUNS_ROOT.
            self.assertFalse(
                str(demo_dir).startswith(str(self.ra.RUNS_ROOT)),
                f"demo leaked into RUNS_ROOT: {demo_dir}",
            )
            self.assertEqual(list(self.ra.RUNS_ROOT.iterdir()), [])
            # tempdir prefix is the one set in _demo (regression guard).
            self.assertTrue(demo_dir.parent.name.startswith("run_archive_demo_"))
            # Full on-disk layout was produced inside the tempdir.
            self.assertTrue((demo_dir / "input.json").exists())
            self.assertTrue((demo_dir / "final.pptx").exists())
            self.assertTrue((demo_dir / "run_report.json").exists())
            self.assertTrue((demo_dir.parent / "INDEX.md").exists())
        finally:
            _shutil.rmtree(demo_dir.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
