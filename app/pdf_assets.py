import io
import re
from typing import Dict, Tuple

import fitz
from PIL import Image

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

    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_text = page.get_text("text") or ""
            all_text.append(page_text)

            for local_img_idx, img_info in enumerate(page.get_images(full=True)):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

                    if pil_img.width < min_width or pil_img.height < min_height:
                        continue

                    fig_id = f"Fig{global_fig_idx}"
                    figures[fig_id] = ExtractedFigure(
                        figure_id=fig_id,
                        caption=_guess_caption_from_page(page_text, local_img_idx),
                        page=page_idx + 1,
                        width=pil_img.width,
                        height=pil_img.height,
                        image_source=pil_to_data_url(pil_img, fmt="PNG", max_width=1200),
                    )
                    global_fig_idx += 1
                except Exception as exc:
                    print(f"extract image failed at page {page_idx + 1}: {exc}")

        text_preview = _clean_text("\n".join(all_text), 12000)
        return text_preview, figures
    finally:
        doc.close()
