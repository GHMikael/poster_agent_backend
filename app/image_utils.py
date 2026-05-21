import base64
import io
import os
from typing import Optional, Tuple

import requests
from PIL import Image


def image_bytes_to_data_url(image_bytes: bytes, fmt: str = "png") -> str:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/{fmt};base64,{encoded}"


def pil_to_data_url(img: Image.Image, fmt: str = "PNG", max_width: int = 1200) -> str:
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)))

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/{fmt.lower()};base64,{encoded}"


def load_image_from_source(source: str) -> Optional[io.BytesIO]:
    if not source:
        return None

    try:
        source = source.strip()

        if source.startswith("data:image/"):
            encoded = source.split(",", 1)[1]
            return io.BytesIO(base64.b64decode(encoded))

        if source.startswith("/9j/") or source.startswith("iVBOR"):
            return io.BytesIO(base64.b64decode(source))

        if source.startswith("http://") or source.startswith("https://"):
            resp = requests.get(source, timeout=20)
            if resp.status_code == 200:
                return io.BytesIO(resp.content)
            return None

        if os.path.isfile(source):
            with open(source, "rb") as f:
                return io.BytesIO(f.read())

        return None
    except Exception as exc:
        print(f"load_image_from_source failed: {exc}")
        return None


def image_size(stream: io.BytesIO) -> Optional[Tuple[int, int]]:
    try:
        pos = stream.tell()
        img = Image.open(stream)
        size = img.size
        stream.seek(pos)
        return size
    except Exception:
        return None
