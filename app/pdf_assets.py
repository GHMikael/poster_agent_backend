import io
import re
from statistics import pstdev
from typing import Dict, Tuple

import fitz
from PIL import Image, ImageStat

from app.image_utils import pil_to_data_url
from app.models import ExtractedFigure


def _clean_text(text: str, max_len: int = 8000) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:max_len]


def _guess_caption_from_page(page_text: str, fig_index: int) -> str:
    patterns = [
        r"(Figure\s+\d+[:.\s].{0,300})",
        r"(Fig\.\s*\d+[:.\s].{0,300})",
        r"(图\s*\d+[:：.\s].{0,300})",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, page_text or "", flags=re.IGNORECASE)
        if matches:
            return _clean_text(matches[min(fig_index, len(matches) - 1)], 300)

    return ""


def _caption_near_image(page: fitz.Page, rect: fitz.Rect, fallback_index: int) -> str:
    """Find the nearest Figure/Fig caption around an image rectangle."""
    caption_re = re.compile(
        r"((?:Figure|Fig\.?|图)\s*\d+\s*[:：.\-]?\s*.{0,300})",
        flags=re.IGNORECASE,
    )
    candidates = []
    for block in page.get_text("blocks") or []:
        if len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        text = _clean_text(str(text), 360)
        match = caption_re.search(text)
        if not match:
            continue
        brect = fitz.Rect(x0, y0, x1, y1)
        vertical_gap = min(abs(brect.y0 - rect.y1), abs(rect.y0 - brect.y1))
        horizontal_overlap = max(0.0, min(rect.x1, brect.x1) - max(rect.x0, brect.x0))
        overlap_ratio = horizontal_overlap / max(1.0, min(rect.width, brect.width))
        # Captions are usually just below/above the figure and share x-span.
        if vertical_gap <= 120 and overlap_ratio >= 0.25:
            candidates.append((vertical_gap, -overlap_ratio, match.group(1)))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return _clean_text(candidates[0][2], 300)
    return _guess_caption_from_page(page.get_text("text") or "", fallback_index)


def _image_filter_reason(img: Image.Image, *, page_area: float, rect_area: float) -> str:
    """Return empty string for usable figures, otherwise a skip reason."""
    width, height = img.size
    if width < 120 or height < 120:
        return "too_small"
    aspect = width / max(1, height)
    if aspect > 8.0 or aspect < 0.125:
        return "extreme_aspect"
    if page_area > 0 and rect_area / page_area < 0.015:
        return "tiny_on_page"

    sample = img.copy()
    sample.thumbnail((160, 160))
    gray = sample.convert("L")
    stat = ImageStat.Stat(gray)
    if (stat.stddev[0] if stat.stddev else 0.0) < 4.0:
        return "near_blank"

    # Very low color variation catches flat decorative blocks and gradients.
    rgb_stat = ImageStat.Stat(sample.convert("RGB"))
    channel_means = rgb_stat.mean or [0, 0, 0]
    channel_std = rgb_stat.stddev or [0, 0, 0]
    if max(channel_std) < 5.0 and pstdev(channel_means) < 8.0:
        return "low_information"
    return ""


def extract_pdf_assets_from_bytes(
    pdf_bytes: bytes,
    min_width: int = 120,
    min_height: int = 120,
) -> Tuple[str, Dict[str, ExtractedFigure]]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    all_text = []
    figures: Dict[str, ExtractedFigure] = {}
    global_fig_idx = 1
    seen_xrefs = set()
    skipped: Dict[str, int] = {}

    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_text = page.get_text("text") or ""
            all_text.append(page_text)

            page_area = float(page.rect.width * page.rect.height)
            for local_img_idx, img_info in enumerate(page.get_images(full=True)):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

                    rects = page.get_image_rects(xref)
                    rect = rects[0] if rects else fitz.Rect(0, 0, pil_img.width, pil_img.height)
                    if pil_img.width < min_width or pil_img.height < min_height:
                        skipped["too_small"] = skipped.get("too_small", 0) + 1
                        continue
                    reason = _image_filter_reason(
                        pil_img,
                        page_area=page_area,
                        rect_area=float(rect.width * rect.height),
                    )
                    if reason:
                        skipped[reason] = skipped.get(reason, 0) + 1
                        continue

                    fig_id = f"Fig{global_fig_idx}"
                    figures[fig_id] = ExtractedFigure(
                        figure_id=fig_id,
                        caption=_caption_near_image(page, rect, local_img_idx),
                        page=page_idx + 1,
                        width=pil_img.width,
                        height=pil_img.height,
                        image_source=pil_to_data_url(pil_img, fmt="PNG", max_width=1200),
                        source_xref=int(xref),
                        bbox=[round(float(v), 2) for v in (rect.x0, rect.y0, rect.x1, rect.y1)],
                        extraction_note="filtered_pdf_image",
                    )
                    global_fig_idx += 1
                except Exception as exc:
                    print(f"extract image failed at page {page_idx + 1}: {exc}")

        text_preview = _clean_text("\n".join(all_text), 12000)
        if skipped:
            print(f"extract_pdf_assets skipped images: {skipped}")
        return text_preview, figures
    finally:
        doc.close()
