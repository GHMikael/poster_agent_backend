"""Per-iteration history logger for the visual feedback loop.

This module persists the *full* state of each iteration so we can do
offline analysis, ablation studies, and visualisation after the closed
loop finishes. Records are stored as a list of JSON objects on disk; each
record contains:

* ``paper_id``    — identifier of the source paper / poster task
* ``iteration``   — 1-indexed iteration number
* ``layout_json`` — the :class:`~app.models.PosterTask` snapshot used in
  this iteration (or any dict the caller passes)
* ``feedback_list`` — list of SVFP feedback records emitted in this
  iteration (see :mod:`app.vlm_commenter`)
* ``score_dict``  — dict containing ``score`` and any derived metrics

Two interfaces are exposed:

* :func:`log_iteration` — one-shot append. Suitable for scripts that
  generate iterations one by one.
* :class:`HistoryLogger` — stateful helper that buffers writes and
  flushes lazily, useful for batch experiments where many iterations
  share the same log file.

JSON is always written as UTF-8 (``ensure_ascii=False``) with stable key
ordering so that the file is diff-friendly and human-readable for paper
appendices.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Union


PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------


@dataclass
class IterationRecord:
    """A single iteration row written to disk.

    Designed to be permissive: ``layout_json`` and ``feedback_list`` are
    accepted as ``Any`` so the caller can pass either a Pydantic model
    dump or a hand-crafted dict.
    """

    paper_id: str
    iteration: int
    layout_json: Dict[str, Any] = field(default_factory=dict)
    feedback_list: List[Dict[str, Any]] = field(default_factory=list)
    score_dict: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict ready for ``json.dump``."""

        data = asdict(self)
        if not data.get("extra"):
            data.pop("extra", None)
        return data


# ---------------------------------------------------------------------------
# Low-level IO helpers
# ---------------------------------------------------------------------------


def _normalise_record(
    paper_id: str,
    iteration_data: Any,
) -> Dict[str, Any]:
    """Build an :class:`IterationRecord` dict from loose ``iteration_data``."""

    if not isinstance(iteration_data, dict):
        raise TypeError("iteration_data must be a dict")
    iteration = iteration_data.get("iteration")
    if iteration is None:
        raise ValueError("iteration_data must include 'iteration' (int).")
    record = IterationRecord(
        paper_id=str(paper_id),
        iteration=int(iteration),
        layout_json=dict(iteration_data.get("layout_json") or {}),
        feedback_list=list(iteration_data.get("feedback_list") or []),
        score_dict=dict(iteration_data.get("score_dict") or {}),
        extra={
            k: v
            for k, v in iteration_data.items()
            if k not in {"iteration", "layout_json", "feedback_list", "score_dict"}
        },
    )
    return record.to_dict()


def _read_existing(path: Path) -> List[Dict[str, Any]]:
    """Read an existing JSON log file. Returns ``[]`` when missing or empty."""

    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"history log {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(
            f"history log {path} must contain a JSON list at top level, got {type(data).__name__}"
        )
    return data


def _atomic_write_json(path: Path, payload: List[Dict[str, Any]]) -> None:
    """Write ``payload`` to ``path`` atomically (write-temp + rename)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Public functional interface
# ---------------------------------------------------------------------------


def log_iteration(
    paper_id: str,
    iteration_data: Dict[str, Any],
    log_path: PathLike,
    append: bool = True,
) -> Dict[str, Any]:
    """Append a single iteration record to ``log_path``.

    Parameters
    ----------
    paper_id:
        Identifier of the paper/poster being iterated on.
    iteration_data:
        Dict containing at least ``iteration``; optionally ``layout_json``,
        ``feedback_list``, ``score_dict`` and any extra fields (preserved
        under the ``extra`` key).
    log_path:
        Destination JSON file. Parent directories are created as needed.
    append:
        When ``True`` (default), the new record is appended to any existing
        records in the file. When ``False``, the file is overwritten with
        only this record.

    Returns
    -------
    dict
        The normalised record actually written.
    """

    path = Path(log_path)
    record = _normalise_record(paper_id, iteration_data)
    existing = _read_existing(path) if append else []
    existing.append(record)
    _atomic_write_json(path, existing)
    return record


def log_iterations(
    paper_id: str,
    iteration_data_list: Iterable[Dict[str, Any]],
    log_path: PathLike,
    append: bool = True,
) -> List[Dict[str, Any]]:
    """Batch variant of :func:`log_iteration`.

    Writes the whole batch in a single atomic rename, which is significantly
    cheaper than calling :func:`log_iteration` in a loop.
    """

    path = Path(log_path)
    new_records = [_normalise_record(paper_id, data) for data in iteration_data_list]
    existing = _read_existing(path) if append else []
    existing.extend(new_records)
    _atomic_write_json(path, existing)
    return new_records


def load_history(log_path: PathLike) -> List[Dict[str, Any]]:
    """Load all iteration records from ``log_path``."""

    return _read_existing(Path(log_path))


def filter_by_paper(
    log_path: PathLike,
    paper_id: str,
) -> List[Dict[str, Any]]:
    """Return only records matching ``paper_id`` from ``log_path``."""

    return [r for r in load_history(log_path) if r.get("paper_id") == paper_id]


# ---------------------------------------------------------------------------
# Stateful logger
# ---------------------------------------------------------------------------


class HistoryLogger:
    """Stateful logger that buffers writes for batch experiments.

    Parameters
    ----------
    log_path:
        Destination JSON file.
    append:
        Whether to keep existing records in ``log_path`` on first flush.
    autoflush:
        When ``True`` (default), every :meth:`log` call writes immediately.
        Set to ``False`` to accumulate records in memory and flush via
        :meth:`flush` or by leaving the :meth:`session` context manager.

    Example
    -------
    >>> logger = HistoryLogger("static/feedback_log.json", autoflush=False)
    >>> with logger.session():
    ...     logger.log("paper_001", {"iteration": 1, "score_dict": {"score": 7.2}})
    ...     logger.log("paper_001", {"iteration": 2, "score_dict": {"score": 8.6}})
    """

    def __init__(
        self,
        log_path: PathLike,
        append: bool = True,
        autoflush: bool = True,
    ) -> None:
        self.log_path = Path(log_path)
        self.append = append
        self.autoflush = autoflush
        self._buffer: List[Dict[str, Any]] = []
        self._touched: bool = False

    def log(self, paper_id: str, iteration_data: Dict[str, Any]) -> Dict[str, Any]:
        """Buffer or persist a single iteration record."""

        record = _normalise_record(paper_id, iteration_data)
        self._buffer.append(record)
        if self.autoflush:
            self.flush()
        return record

    def log_many(
        self,
        paper_id: str,
        iteration_data_list: Iterable[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Buffer or persist multiple records for the same paper."""

        new_records = [_normalise_record(paper_id, data) for data in iteration_data_list]
        self._buffer.extend(new_records)
        if self.autoflush:
            self.flush()
        return new_records

    def flush(self) -> Path:
        """Persist buffered records to disk and return the log path.

        Subsequent flushes always append to whatever is already on disk.
        """

        if not self._buffer:
            return self.log_path
        existing = _read_existing(self.log_path) if (self.append or self._touched) else []
        existing.extend(self._buffer)
        _atomic_write_json(self.log_path, existing)
        self._buffer.clear()
        self._touched = True
        return self.log_path

    def load(self) -> List[Dict[str, Any]]:
        """Load whatever is currently persisted on disk."""

        return _read_existing(self.log_path)

    @contextmanager
    def session(self) -> Generator["HistoryLogger", None, None]:
        """Context manager that flushes the buffer on exit (success or error)."""

        try:
            yield self
        finally:
            self.flush()


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------


def _demo(tmp_dir: Optional[PathLike] = None) -> Path:
    """Write a tiny synthetic log so users can see the on-disk format."""

    base = Path(tmp_dir) if tmp_dir else Path(tempfile.mkdtemp(prefix="history_logger_demo_"))
    log_path = base / "feedback_history.json"

    log_iteration(
        paper_id="2604.05005v2",
        iteration_data={
            "iteration": 1,
            "layout_json": {"template": "template_dashboard", "panels": 6},
            "feedback_list": [
                {
                    "issue_type": "dense_content",
                    "details": "Too many bullets in Method panel.",
                    "suggested_fix": "reduce_bullet_count",
                }
            ],
            "score_dict": {"score": 7.2, "vlm_source": "preview_vlm_or_heuristic"},
        },
        log_path=log_path,
        append=False,
    )

    logger = HistoryLogger(log_path, autoflush=False)
    with logger.session():
        logger.log(
            paper_id="2604.05005v2",
            iteration_data={
                "iteration": 2,
                "layout_json": {"template": "template_dashboard", "panels": 6},
                "feedback_list": [
                    {
                        "issue_type": "low_contrast",
                        "details": "Header text lacks contrast.",
                        "suggested_fix": "increase_contrast",
                    }
                ],
                "score_dict": {"score": 8.4, "delta": 1.2},
            },
        )
        logger.log_many(
            paper_id="2604.05005v2",
            iteration_data_list=[
                {
                    "iteration": 3,
                    "layout_json": {"template": "template_dashboard", "panels": 6},
                    "feedback_list": [],
                    "score_dict": {"score": 9.1, "delta": 0.7, "converged": True},
                }
            ],
        )

    print(f"Wrote {len(load_history(log_path))} records to {log_path}")
    return log_path


if __name__ == "__main__":
    _demo()
