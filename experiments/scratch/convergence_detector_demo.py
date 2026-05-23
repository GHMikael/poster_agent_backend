"""Demo: print convergence states for a synthetic 3-iteration run.

Originally lived in ``app/feedback_loop.py::_convergence_demo``. Moved here
so the production module no longer carries a ``__main__`` entry point.

Run with::

    .venv312/bin/python -m experiments.scratch.convergence_detector_demo
"""

from __future__ import annotations

import json

from app.feedback_loop import check_convergence


def main() -> None:
    fake_run = [
        {"score": 6.4, "feedback": {"global_issues": ["dense_content"], "panel_feedback": []}},
        {"score": 7.6, "feedback": {"global_issues": ["dense_content"], "panel_feedback": []}},
        {"score": 7.7, "feedback": {"global_issues": [], "panel_feedback": []}},
    ]
    print(json.dumps(check_convergence(fake_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
