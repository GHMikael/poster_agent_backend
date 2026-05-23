"""Experiment-mode telemetry: per-stage latency, LLM tokens, soffice exits.

This is the *only* coupling point between ``app/`` (production) and
``experiments/`` (offline). The production path imports
:func:`get_logger_from_env` lazily; when ``POSTER_EXPERIMENT_MODE`` is unset
it returns ``None`` and every caller short-circuits with a single
``if logger is None`` check, so production has zero per-call overhead.

When the env is set, a :class:`JsonlExperimentLogger` writes one JSON object
per event to ``$POSTER_EXPERIMENT_LOG``. Each event has at least:

* ``ts``     — ISO-8601 UTC timestamp
* ``kind``   — ``stage`` | ``llm_call`` | ``soffice``
* ``run_id`` — propagated from the caller (poster run id, set on the logger)
* ``stage``  — short, stable identifier (``vlm_call``, ``pptx_gen``, …)

Consumers (``experiments/metrics/d1_latency.py``, ``d2_cost.py``,
``d3_failure_rate.py``) parse the JSONL without needing to import
production code.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Union


PathLike = Union[str, Path]


__all__ = [
    "ExperimentLogger",
    "JsonlExperimentLogger",
    "NullExperimentLogger",
    "get_logger_from_env",
]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ExperimentLogger(Protocol):
    """Minimal interface used by ``app/feedback_loop.py`` and ``app/vlm_commenter.py``.

    All methods are keyword-only so callers can add fields later without
    breaking the protocol. Implementations must be **thread-safe** (the
    feedback loop runs background workers).
    """

    run_id: str

    def log_stage(
        self,
        *,
        stage: str,
        latency_ms: float,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    def log_llm_call(
        self,
        *,
        stage: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        raw_response: Optional[Dict[str, Any]] = None,
        retries: int = 0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    def log_soffice(
        self,
        *,
        exit_code: int,
        stderr: str,
        latency_ms: float,
        attempt: int = 1,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class JsonlExperimentLogger:
    """Append-mode JSONL writer. One file per run."""

    log_path: Path
    run_id: str = ""
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.log_path = Path(self.log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Touch file so consumers can tail before any event is emitted.
        if not self.log_path.exists():
            self.log_path.write_text("", encoding="utf-8")

    def _emit(self, payload: Dict[str, Any]) -> None:
        payload.setdefault("ts", _utc_now())
        payload.setdefault("run_id", self.run_id)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")

    def log_stage(
        self,
        *,
        stage: str,
        latency_ms: float,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        ev: Dict[str, Any] = {"kind": "stage", "stage": stage, "latency_ms": float(latency_ms)}
        if extra:
            ev["extra"] = extra
        self._emit(ev)

    def log_llm_call(
        self,
        *,
        stage: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        raw_response: Optional[Dict[str, Any]] = None,
        retries: int = 0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        ev: Dict[str, Any] = {
            "kind": "llm_call",
            "stage": stage,
            "model": model,
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(prompt_tokens) + int(completion_tokens),
            "latency_ms": float(latency_ms),
            "retries": int(retries),
        }
        if raw_response is not None:
            ev["raw_response"] = raw_response
        if extra:
            ev["extra"] = extra
        self._emit(ev)

    def log_soffice(
        self,
        *,
        exit_code: int,
        stderr: str,
        latency_ms: float,
        attempt: int = 1,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        ev: Dict[str, Any] = {
            "kind": "soffice",
            "exit_code": int(exit_code),
            "stderr": (stderr or "")[-2000:],
            "latency_ms": float(latency_ms),
            "attempt": int(attempt),
        }
        if extra:
            ev["extra"] = extra
        self._emit(ev)

    def close(self) -> None:
        # Lines are flushed per-write; nothing to close.
        return None


class NullExperimentLogger:
    """No-op logger. Returned when env is unset. All methods do nothing.

    Production never instantiates this — production callers receive
    ``None`` and skip via a single check — but tests benefit from a
    drop-in stand-in that implements the Protocol.
    """

    run_id: str = ""

    def log_stage(self, **_kwargs: Any) -> None: ...
    def log_llm_call(self, **_kwargs: Any) -> None: ...
    def log_soffice(self, **_kwargs: Any) -> None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Env-gated factory
# ---------------------------------------------------------------------------


def get_logger_from_env(
    run_id: str = "",
    *,
    default_log_path: Optional[PathLike] = None,
) -> Optional[ExperimentLogger]:
    """Return a :class:`JsonlExperimentLogger` iff ``POSTER_EXPERIMENT_MODE``
    is truthy (``1``/``true``/``yes``), else ``None``.

    Log path resolution priority:

    1. ``POSTER_EXPERIMENT_LOG`` env var (explicit override; CI/debug use)
    2. ``default_log_path`` argument (production callers pass the
       per-run archive path so each run gets its own file)
    3. ``./experiment_log.jsonl`` relative to CWD (last-resort).
    """

    flag = (os.environ.get("POSTER_EXPERIMENT_MODE") or "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    env_path = os.environ.get("POSTER_EXPERIMENT_LOG")
    if env_path:
        log_path = Path(env_path)
    elif default_log_path is not None:
        log_path = Path(default_log_path)
    else:
        log_path = Path("experiment_log.jsonl")
    return JsonlExperimentLogger(log_path=log_path, run_id=run_id)
