import base64
import shutil
import uuid
from pathlib import Path
from typing import Dict

from PIL import Image

from app.config import ASSET_PATH
from app.models import ExtractedFigure, FigureAsset, PosterTask


def _decode_data_url(data_url: str) -> bytes:
    if not data_url.startswith("data:image/"):
        raise ValueError("image_source is not a data URL")
    return base64.b64decode(data_url.split(",", 1)[1])


def persist_extracted_figures(figures: Dict[str, ExtractedFigure]) -> str:
    asset_token = uuid.uuid4().hex[:16]
    asset_dir = ASSET_PATH / asset_token
    asset_dir.mkdir(parents=True, exist_ok=True)

    for figure_id, figure in figures.items():
        if not figure.image_source:
            continue

        image_path = asset_dir / f"{figure_id}.png"
        image_path.write_bytes(_decode_data_url(figure.image_source))

        thumb_path = asset_dir / f"{figure_id}_thumb.jpg"
        try:
            img = Image.open(image_path).convert("RGB")
            img.thumbnail((420, 320))
            img.save(thumb_path, format="JPEG", quality=72, optimize=True)
        except Exception:
            shutil.copyfile(image_path, thumb_path)

    return asset_token


def public_asset_url(asset_token: str, filename: str, base_url: str = "") -> str:
    path = f"/assets/{asset_token}/{filename}"
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"


def strip_heavy_image_sources(
    figures: Dict[str, ExtractedFigure],
    asset_token: str,
    base_url: str = "",
    include_images: bool = False,
) -> Dict[str, dict]:
    light_figures = {}
    for figure_id, figure in figures.items():
        data = figure.model_dump()
        data["image_url"] = public_asset_url(asset_token, f"{figure_id}.png", base_url)
        data["thumbnail_url"] = public_asset_url(asset_token, f"{figure_id}_thumb.jpg", base_url)
        if not include_images:
            data.pop("image_source", None)
        light_figures[figure_id] = data
    return light_figures


def hydrate_task_image_sources(task: PosterTask) -> PosterTask:
    if not task.asset_token:
        return task

    for figure_id, figure in task.figures.items():
        if figure.image_source:
            continue

        image_path = ASSET_PATH / task.asset_token / f"{figure_id}.png"
        if image_path.exists():
            figure.image_source = str(image_path)

    for panel in task.panels:
        if not panel.figure_id or panel.figure_id in task.figures:
            continue
        image_path = ASSET_PATH / task.asset_token / f"{panel.figure_id}.png"
        if image_path.exists():
            task.figures[panel.figure_id] = FigureAsset(
                caption=panel.figure_caption,
                image_source=str(image_path),
            )

    return task
