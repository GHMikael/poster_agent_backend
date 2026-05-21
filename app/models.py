from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class FigureAsset(BaseModel):
    caption: str = ""
    type: str = "other"
    description: str = ""
    best_matched_section: str = ""
    importance: str = "medium"
    image_source: str = ""
    image_url: str = ""
    thumbnail_url: str = ""


class Panel(BaseModel):
    section: str
    content: List[str] = Field(default_factory=list)
    figure: Optional[str] = None
    figure_id: str = ""
    figure_caption: str = ""
    # Allowed values: "text_only", "text_top_image_bottom", "image_compact".
    # "image_compact" tells the renderer to shrink the figure container so
    # text occupies most of the panel — used by the feedback loop to fix
    # large padding around small figures.
    layout_hint: str = "text_only"
    # Per-panel body font multiplier, mutated by the feedback loop when
    # the panel is too sparse (>1.0) or too crowded (<1.0). Clamped to
    # [0.7, 1.3] by the renderer.
    body_font_scale: float = 1.0


class PosterLayout(BaseModel):
    page_size: str = "A3"
    layout_type: str = "dashboard_grid"
    reading_order: str = "top_to_bottom_left_to_right"


class PosterTask(BaseModel):
    asset_token: str = ""
    template: str = "template_dashboard"
    # Template-specific layout variant. "auto" lets the renderer pick based
    # on panel/figure structure; feedback can set values like
    # "story_columns", "story_spotlight" or "story_zigzag".
    layout_variant: str = "auto"
    color_theme: str = "academic_blue"
    # Controls extra visual hierarchy such as metric pills, emphasis bars
    # and decorative section markers.
    emphasis_level: str = "normal"
    poster_title: str = "Academic Poster"
    authors: str = ""
    paper_info: str = ""
    layout: PosterLayout = Field(default_factory=PosterLayout)
    panels: List[Panel]
    figures: Dict[str, FigureAsset] = Field(default_factory=dict)
    use_commenter: bool = False
    max_iterations: int = 2
    save_debug_images: bool = True
    # Global font multiplier for header + body. Applied on top of
    # Panel.body_font_scale. Clamped to [0.8, 1.2] by the renderer.
    global_font_scale: float = 1.0


class ExtractedFigure(BaseModel):
    figure_id: str
    caption: str = ""
    page: int
    width: int
    height: int
    image_source: str = ""
    image_url: str = ""
    thumbnail_url: str = ""


class PdfAssetResponse(BaseModel):
    asset_token: str = ""
    text_preview: str
    figures: Dict[str, ExtractedFigure]
