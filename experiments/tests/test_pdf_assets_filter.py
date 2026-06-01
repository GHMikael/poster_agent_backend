from PIL import Image, ImageDraw

from app.pdf_assets import _image_filter_reason


def test_pdf_asset_filter_rejects_blank_image():
    img = Image.new("RGB", (300, 220), "white")

    assert _image_filter_reason(img, page_area=1000 * 1000, rect_area=300 * 220) == "near_blank"


def test_pdf_asset_filter_accepts_informative_diagram():
    img = Image.new("RGB", (420, 280), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((30, 40, 160, 120), outline="black", width=4)
    draw.rectangle((250, 160, 390, 245), outline="blue", width=4)
    draw.line((160, 80, 250, 200), fill="red", width=5)
    draw.text((45, 75), "Model", fill="black")

    assert _image_filter_reason(img, page_area=1000 * 1000, rect_area=420 * 280) == ""


def test_pdf_asset_filter_rejects_extreme_aspect_ratio():
    img = Image.new("RGB", (1200, 130), "black")

    assert _image_filter_reason(img, page_area=1000 * 1000, rect_area=1200 * 130) == "extreme_aspect"
