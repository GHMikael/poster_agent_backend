"""Paper2Poster (Gao et al., NeurIPS 2024) — external SOTA baseline.

Implementation is a subprocess wrapper around the vendored upstream
repo. The repo is NOT committed; ``bootstrap_vendor.sh`` clones it to
``experiments/baselines/_vendor/Paper2Poster/`` at a pinned commit.

The wrapper:

1. Spawns ``python paper2poster/main.py --pdf <paper_path> --out <tmp>``
   with the env from ``baselines.yaml``.
2. Captures stdout / stderr for the cell's metadata.
3. Locates the produced ``poster.pptx`` (filename varies per release;
   we ``glob('*.pptx')``).
4. Copies it into our cell folder and renders the PNG via the shared
   ``render_pptx_to_png_via_app`` helper so all baselines have the same
   rendering for visual metrics.

Subprocess token / latency capture is best-effort: D2 cost cannot be
inferred from an opaque subprocess; we record only wall-clock time and
let D2 report 0 (with a note in the paper).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from experiments.baselines.base import BaselineRunner, PosterArtifact


__all__ = ["Paper2PosterRunner"]


class Paper2PosterRunner(BaselineRunner):
    name = "paper2poster"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.vendor_dir = Path(self.config.get("vendor_dir", "experiments/baselines/_vendor/Paper2Poster"))
        self.entry_cmd_tmpl = self.config.get(
            "entry_cmd", "python paper2poster/main.py --pdf {paper_path} --out {out_dir}"
        )

    def run(
        self,
        paper_path: Path,
        out_dir: Path,
        *,
        timeout_s: int = 1800,
    ) -> PosterArtifact:
        cell_dir, log_path, meta, t0 = self._begin(paper_path, out_dir)
        if not self.vendor_dir.exists():
            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=None, panels_json=None, log_path=log_path,
                exit_code=2,
                error=f"vendor repo not bootstrapped at {self.vendor_dir}; run experiments/baselines/bootstrap_vendor.sh",
            )

        try:
            staging = cell_dir / "_vendor_out"
            staging.mkdir(exist_ok=True)
            cmd = self.entry_cmd_tmpl.format(paper_path=str(paper_path), out_dir=str(staging))
            proc = subprocess.run(
                cmd, shell=True, cwd=str(self.vendor_dir),
                capture_output=True, text=True, timeout=timeout_s,
            )
            (cell_dir / "vendor_stdout.log").write_text(proc.stdout or "", encoding="utf-8")
            (cell_dir / "vendor_stderr.log").write_text(proc.stderr or "", encoding="utf-8")

            if proc.returncode != 0:
                return self._finish(
                    cell_dir=cell_dir, meta=meta, t0=t0,
                    pptx_path=None, panels_json=None, log_path=log_path,
                    exit_code=proc.returncode,
                    error=f"vendor exit={proc.returncode}; stderr tail: {(proc.stderr or '')[-400:]}",
                )

            produced = sorted(staging.glob("*.pptx"))
            if not produced:
                return self._finish(
                    cell_dir=cell_dir, meta=meta, t0=t0,
                    pptx_path=None, panels_json=None, log_path=log_path,
                    exit_code=3, error="vendor produced no .pptx",
                )
            dest_pptx = cell_dir / "poster.pptx"
            shutil.copy2(produced[0], dest_pptx)

            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=dest_pptx,
                panels_json=None,            # opaque baseline; A1/A2/A3 will OCR (M3 path)
                log_path=log_path,
            )
        except subprocess.TimeoutExpired:
            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=None, panels_json=None, log_path=log_path,
                exit_code=-9, error=f"timeout after {timeout_s}s",
            )
        except Exception as exc:
            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=None, panels_json=None, log_path=log_path,
                exit_code=1, error=f"{type(exc).__name__}: {exc}",
            )
