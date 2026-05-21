import os
from pathlib import Path

from dotenv import load_dotenv
from pptx.dml.color import RGBColor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / ".env.example")

PORT = int(os.getenv("PORT", "8000"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "outputs")

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
QWEN_VL_MODEL = os.getenv("QWEN_VL_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")

OUTPUT_PATH = PROJECT_ROOT / OUTPUT_DIR
ASSET_PATH = PROJECT_ROOT / "static" / "assets"
OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
ASSET_PATH.mkdir(parents=True, exist_ok=True)

C_NAVY = RGBColor(0, 48, 135)
C_BLUE = RGBColor(0, 86, 179)
C_LIGHT_BLUE = RGBColor(235, 244, 255)
C_PALE_BLUE = RGBColor(244, 248, 255)
C_WHITE = RGBColor(255, 255, 255)
C_TEXT = RGBColor(30, 41, 59)
C_MUTED = RGBColor(100, 116, 139)
C_RED = RGBColor(220, 38, 38)
C_GREEN = RGBColor(22, 163, 74)
C_ORANGE = RGBColor(234, 88, 12)
C_PURPLE = RGBColor(126, 34, 206)
C_BORDER = RGBColor(180, 200, 230)
