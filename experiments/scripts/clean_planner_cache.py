#!/usr/bin/env python3
"""B1c 止血 — 基于 figure_audit.json 清洗 planner_cache 的错位/缺失图。

读 ``experiments/results/figure_audit.json``(由 audit_figures.py 产出),
把所有不可安全渲染的图从 planner_cache 的 panel 引用里摘掉:该 panel
退化为 text_only(figure_id/figure/figure_caption 清空),并在 figures 字典里
给这些图打 ``audit_status`` 标记(便于 future 的图复用率指标过滤)。
status=="weak" 的图保留不动。

**零额外 VLM 调用**(复用已有 audit),不重跑 Dify。清洗后需重跑 baseline
渲染 + 重算 a2/a3 才能在指标上反映干净结果。

安全:默认 **dry-run 只预览**;加 ``--apply`` 才写回,且首次 --apply 会先把整个
planner_cache 备份到 planner_cache_raw/(已存在则跳过备份、拒绝覆盖以防二次清洗)。

用法::

    python experiments/scripts/clean_planner_cache.py                 # 预览
    python experiments/scripts/clean_planner_cache.py --apply         # 备份+清洗
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT = REPO_ROOT / "experiments" / "results" / "figure_audit.json"
DEFAULT_CACHE = REPO_ROOT / "experiments" / "datasets" / "planner_cache"
DEFAULT_BACKUP = REPO_ROOT / "experiments" / "datasets" / "planner_cache_raw"


UNSAFE_STATUSES = {"broken", "missing_image_file", "missing_figure_entry"}


def _unsafe_by_paper(audit_path: Path) -> Dict[str, Dict[str, str]]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, str]] = {}
    for r in audit.get("results", []):
        for f in r.get("figures", []):
            status = str(f.get("status") or "")
            if status in UNSAFE_STATUSES:
                out.setdefault(r["paper"], {})[str(f["figure_id"])] = status
    return out


def clean_doc(doc: dict, unsafe: Dict[str, str]) -> int:
    """Demote panels referencing unsafe figures to text_only. Returns #panels changed."""
    n = 0
    for panel in doc.get("panels", []) or []:
        if str(panel.get("figure_id") or "") in unsafe:
            panel["figure_id"] = ""
            panel["figure"] = None
            panel["figure_caption"] = ""
            panel["layout_hint"] = "text_only"
            n += 1
    # tag (don't delete) unsafe figures so later metrics can filter them
    figures = doc.get("figures") or {}
    for fid, status in unsafe.items():
        if fid in figures and isinstance(figures[fid], dict):
            figures[fid]["audit_status"] = status
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Demote broken figures in planner_cache (B1c stop-the-bleed).")
    ap.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run preview)")
    args = ap.parse_args()

    if not args.audit.exists():
        print(f"[!] missing audit file {args.audit}; run audit_figures.py first", file=sys.stderr)
        return 1

    unsafe_by_paper = _unsafe_by_paper(args.audit)
    total_unsafe = sum(len(v) for v in unsafe_by_paper.values())
    print(f"[*] {'APPLY' if args.apply else 'DRY-RUN'}: {total_unsafe} unsafe figures across "
          f"{len(unsafe_by_paper)} papers")

    if args.apply:
        if args.backup_dir.exists():
            print(f"[!] backup {args.backup_dir} already exists — refusing to re-clean "
                  f"(would double-clean / lose raw). Remove it manually if you really intend to.", file=sys.stderr)
            return 1
        shutil.copytree(args.cache_dir, args.backup_dir)
        print(f"[*] backed up raw cache → {args.backup_dir}")

    print("-" * 80)
    total_panels_changed = 0
    for stem, unsafe in sorted(unsafe_by_paper.items()):
        path = args.cache_dir / f"{stem}.json"
        if not path.exists():
            print(f"  [skip] {stem}: cache file not found")
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        n = clean_doc(doc, unsafe)
        total_panels_changed += n
        labels = [f"{fid}:{status}" for fid, status in sorted(unsafe.items())]
        print(f"  {stem[:44]:<44}  demote {n} panel(s)  (unsafe figs: {labels})")
        if args.apply:
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 80)
    print(f"[=] {total_panels_changed} panels demoted to text_only across {len(unsafe_by_paper)} papers")
    if not args.apply:
        print("[i] dry-run only. Re-run with --apply to write (raw cache will be backed up first).")
    else:
        print(f"[OK] cache cleaned in place. Raw preserved at {args.backup_dir}.")
        print("[next] re-run baselines (render) + recompute a2/a3 to reflect the clean cache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
