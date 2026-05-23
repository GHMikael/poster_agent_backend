"""Smoke tests for the env-gated experiment logger.

Run via::

    .venv312/bin/python -m unittest experiments.tests.test_experiment_logger
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from experiments.tools.experiment_logger import (
    JsonlExperimentLogger,
    NullExperimentLogger,
    get_logger_from_env,
)


class ExperimentLoggerEnvGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in ("POSTER_EXPERIMENT_MODE", "POSTER_EXPERIMENT_LOG")}
        os.environ.pop("POSTER_EXPERIMENT_MODE", None)
        os.environ.pop("POSTER_EXPERIMENT_LOG", None)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_unset_returns_none(self) -> None:
        self.assertIsNone(get_logger_from_env())

    def test_falsey_returns_none(self) -> None:
        os.environ["POSTER_EXPERIMENT_MODE"] = "0"
        self.assertIsNone(get_logger_from_env())
        os.environ["POSTER_EXPERIMENT_MODE"] = ""
        self.assertIsNone(get_logger_from_env())

    def test_set_returns_logger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["POSTER_EXPERIMENT_MODE"] = "1"
            os.environ["POSTER_EXPERIMENT_LOG"] = str(Path(tmp) / "x.jsonl")
            logger = get_logger_from_env(run_id="r1")
            self.assertIsNotNone(logger)
            self.assertEqual(logger.run_id, "r1")  # type: ignore[union-attr]


class JsonlExperimentLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_path = Path(self._tmp.name) / "log.jsonl"
        self.logger = JsonlExperimentLogger(log_path=self.log_path, run_id="run-007")

    def _read(self):
        return [json.loads(l) for l in self.log_path.read_text(encoding="utf-8").splitlines() if l]

    def test_log_stage_writes_one_line(self) -> None:
        self.logger.log_stage(stage="vlm_call", latency_ms=123.4)
        events = self._read()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["kind"], "stage")
        self.assertEqual(ev["stage"], "vlm_call")
        self.assertAlmostEqual(ev["latency_ms"], 123.4)
        self.assertEqual(ev["run_id"], "run-007")
        self.assertIn("ts", ev)

    def test_log_llm_call_records_tokens_and_total(self) -> None:
        self.logger.log_llm_call(
            stage="vlm_layout_judge",
            model="gpt-4o",
            prompt_tokens=1234,
            completion_tokens=222,
            latency_ms=987.0,
            raw_response={"choices": [{"finish_reason": "stop"}]},
        )
        ev = self._read()[0]
        self.assertEqual(ev["kind"], "llm_call")
        self.assertEqual(ev["model"], "gpt-4o")
        self.assertEqual(ev["total_tokens"], 1456)
        self.assertEqual(ev["raw_response"]["choices"][0]["finish_reason"], "stop")

    def test_log_soffice_truncates_long_stderr(self) -> None:
        long_err = "x" * 5000
        self.logger.log_soffice(exit_code=-6, stderr=long_err, latency_ms=12.0, attempt=2)
        ev = self._read()[0]
        self.assertEqual(ev["kind"], "soffice")
        self.assertEqual(ev["exit_code"], -6)
        self.assertEqual(ev["attempt"], 2)
        self.assertLessEqual(len(ev["stderr"]), 2000)

    def test_thread_safety_serialises_concurrent_writes(self) -> None:
        import threading

        def worker(i: int) -> None:
            self.logger.log_stage(stage=f"s{i}", latency_ms=float(i))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        events = self._read()
        self.assertEqual(len(events), 50)


class NullExperimentLoggerTests(unittest.TestCase):
    def test_methods_do_not_raise(self) -> None:
        n = NullExperimentLogger()
        n.log_stage(stage="x", latency_ms=1.0)
        n.log_llm_call(stage="x", model="m", prompt_tokens=1, completion_tokens=1, latency_ms=1.0)
        n.log_soffice(exit_code=0, stderr="", latency_ms=1.0)
        n.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
