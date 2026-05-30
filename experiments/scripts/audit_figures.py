#!/usr/bin/env python3
"""B1a — 图污染体检 (figure pollution audit).

诊断 planner_cache 里"图字节 ↔ caption 元数据"错位的范围。根因见
``app/pdf_assets.py`` 的 ``extract_pdf_assets_from_bytes``:它按尺寸提取
页面所有嵌入图并机械编号 FigN,不区分正文图表与自然图像样例/装饰图,
导致 caption 描述的图与 figure_id 指向的实际字节错位
(已确认 多模态预训练_4DE028 的 Fig5=小猫、Fig6=日落)。

本脚本复用 A2 的 VLM 对齐打分 (``experiments.judges.altclip_judge.alignment_score``):
对每个被 panel 引用的 figure,让 Qwen-VL 给"图 ↔ caption+bullets"打 0-5 分。
分档:
    score <= --threshold-broken (默认 1.5)  → broken  (几乎确定错图)
    score <  --threshold-weak   (默认 3.0)  → weak    (弱相关/装饰)
    否则                                      → ok

vlm_chat 自带缓存:已跑过 A2 的论文会命中缓存、不重复计费。

用法 (从仓库根目录运行)::

    # 无网先验证逻辑 + 摸清规模(不调 VLM)
    python experiments/scripts/audit_figures.py --dry-run

    # 真体检(需要 Qwen-VL API / 网络);先小规模验证再全量
    python experiments/scripts/audit_figures.py --limit 3
    python experiments/scripts/audit_figures.py

结果写入 ``experiments/results/figure_audit.json``。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Lazy / tolerant import: --dry-run must work even if VLM deps are unavailable.
try:
    from experiments.judges.altclip_judge import alignment_score
except Exception as _exc:  # pragma: no cover - import-time env issues
    alignment_score = None
    _IMPORT_ERROR = _exc
else:
    _IMPORT_ERROR = None

DEFAULT_CACHE_DIR = REPO_ROOT / "experiments" / "datasets" / "planner_cache"
DEFAULT_OUT = REPO_ROOT / "experiments" / "results" / "figure_audit.json"


def _referenced_figures(doc: dict):
    """Yield (panel, figure_id, figure_dict|None) for every panel that declares a figure."""
    panels = doc.get("panels") or []
    figures = doc.get("figures") or {}
    for panel in panels:
        fid = str(panel.get("figure_id") or "").strip()
        if not fid:
            continue
        yield panel, fid, figures.get(fid)


def audit_paper(path: Path, *, threshold_broken: float, threshold_weak: float, dry_run: bool) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    title = doc.get("poster_title") or path.stem
    figures_out = []

    for panel, fid, fig in _referenced_figures(doc):
        section = str(panel.get("section") or "")
        caption = str(panel.get("figure_caption") or (fig or {}).get("caption") or "")
        src = str((fig or {}).get("image_source") or "")
        exists = bool(src) and Path(src).exists()
        rec = {
            "figure_id": fid,
            "section": section,
            "caption": caption[:140],
            "image_source": src,
            "exists": exists,
            "score": None,
            "status": "",
            "rationale": "",
        }

        if fig is None:
            rec["status"] = "missing_figure_entry"
        elif not exists:
            rec["status"] = "missing_image_file"
        elif dry_run:
            rec["status"] = "pending"
            rec["rationale"] = "(dry-run: not scored)"
        else:
            if alignment_score is None:
                raise RuntimeError(f"alignment_score import failed: {_IMPORT_ERROR}")
            bullets = [str(b) for b in (panel.get("content") or []) if str(b).strip()]
            try:
                res = alignment_score(
                    image_path=Path(src),
                    section=section,
                    bullets=bullets,
                    caption=caption,
                )
                score = float(res["score"])
                rec["score"] = score
                rec["rationale"] = str(res.get("rationale", ""))[:200]
                if score <= threshold_broken:
                    rec["status"] = "broken"
                elif score < threshold_weak:
                    rec["status"] = "weak"
                else:
                    rec["status"] = "ok"
            except Exception as exc:  # network / API failure — record, don't abort
                rec["status"] = "error"
                rec["rationale"] = f"({type(exc).__name__}: {exc})"[:200]

        figures_out.append(rec)

    return {
        "paper": path.stem,
        "title": title,
        "n_referenced": len(figures_out),
        "figures": figures_out,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit planner_cache figure caption↔content mismatch.")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--limit", type=int, default=0, help="only first N papers (0 = all)")
    ap.add_argument("--threshold-broken", type=float, default=1.5)
    ap.add_argument("--threshold-weak", type=float, default=3.0)
    ap.add_argument("--dry-run", action="store_true", help="skip VLM calls; just check structure & files")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    files = sorted(args.cache_dir.glob("*.json"))
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"[!] no cache json under {args.cache_dir}")
        return 1

    print(f"[*] auditing {len(files)} papers from {args.cache_dir}"
          + ("  (DRY RUN — no VLM calls)" if args.dry_run else ""))
    print("-" * 96)

    results = []
    status_counter: Counter = Counter()
    papers_with_broken = set()
    t0 = time.time()

    for path in files:
        res = audit_paper(
            path,
            threshold_broken=args.threshold_broken,
            threshold_weak=args.threshold_weak,
            dry_run=args.dry_run,
        )
        results.append(res)
        per = Counter(f["status"] for f in res["figures"])
        status_counter.update(per)
        if per.get("broken"):
            papers_with_broken.add(res["paper"])
        flag = " <<< BROKEN" if per.get("broken") else ""
        print(f"  {res['paper'][:46]:<46}  figs={res['n_referenced']:<2}  "
              f"ok={per.get('ok',0)} weak={per.get('weak',0)} broken={per.get('broken',0)} "
              f"miss={per.get('missing_image_file',0)+per.get('missing_figure_entry',0)} "
              f"err={per.get('error',0)} pend={per.get('pending',0)}{flag}")

    total_figs = sum(r["n_referenced"] for r in results)
    summary = {
        "n_papers": len(results),
        "n_referenced_figures": total_figs,
        "by_status": dict(status_counter),
        "papers_with_broken_figure": sorted(papers_with_broken),
        "n_papers_with_broken": len(papers_with_broken),
        "thresholds": {"broken<=": args.threshold_broken, "weak<": args.threshold_weak},
        "dry_run": args.dry_run,
        "elapsed_s": round(time.time() - t0, 1),
    }

    print("-" * 96)
    print(f"[=] papers={summary['n_papers']}  referenced_figures={total_figs}  "
          f"by_status={summary['by_status']}")
    if not args.dry_run:
        broken_n = status_counter.get("broken", 0)
        rate = (broken_n / total_figs * 100) if total_figs else 0.0
        print(f"[=] BROKEN figures: {broken_n}/{total_figs} ({rate:.0f}%)  "
              f"|  polluted papers: {summary['n_papers_with_broken']}/{summary['n_papers']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "results": results},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] written → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
