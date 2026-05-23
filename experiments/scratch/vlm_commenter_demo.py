"""Demo: emit a batch of SVFP feedback records and dump the JSON schema.

Originally lived in ``app/vlm_commenter.py::_example_demo``. Moved here so
the production module no longer carries a ``__main__`` entry point.

Run with::

    .venv312/bin/python -m experiments.scratch.vlm_commenter_demo
"""

from __future__ import annotations

import json

from app.vlm_commenter import (
    SVFPIssueType,
    SVFPSuggestedAction,
    build_feedback_batch,
    get_svfp_schema,
)


def main() -> None:
    items = build_feedback_batch(
        [
            {
                "issue_type": SVFPIssueType.OVERLAPPING_ELEMENTS,
                "details": "Panel 'Method' shows 7 bullets, the last two overflow the card.",
                "section": "Method",
                "severity": "high",
                "suggested_fix": SVFPSuggestedAction.REDUCE_BULLET_COUNT.value,
                "target_value": 4,
            },
            {
                "issue_type": "low_contrast",
                "details": "Title uses light gray on white, contrast ratio ≈ 2.1.",
                "suggested_fix": SVFPSuggestedAction.SWITCH_PALETTE.value,
                "section": "Header",
                "severity": "medium",
            },
            {
                "issue_type": SVFPIssueType.EMPTY_SPACE,
                "details": "Results panel has small figure with large surrounding padding.",
                "suggested_fix": SVFPSuggestedAction.COMPACT_FIGURE_BOX.value,
                "section": "Results",
            },
        ]
    )

    print("=== SVFP feedback batch ===")
    print(json.dumps(items, ensure_ascii=False, indent=2))

    print("\n=== SVFP single-item JSON Schema ===")
    print(json.dumps(get_svfp_schema(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
