"""Pricing table (USD per 1k tokens) for cost accounting (D2).

Values reflect public OpenAI / Anthropic / DashScope pricing as of 2026-05.
Update before every paper revision. Test-time references should pin a
specific date by using ``price(model, date='2026-05-23')``.

Cost convention: ``input`` covers prompt + tool-use input, ``output``
covers completion (incl. reasoning tokens for o-series models). Image
inputs are billed per the OpenAI vision tier (we approximate as input
tokens since the SDK already returns ``usage.prompt_tokens`` that
includes vision token count for GPT-4o).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class ModelPrice:
    input_per_1k_usd: float
    output_per_1k_usd: float


# Keys are the *exact* model identifiers we pass to the SDKs.
PRICING: Dict[str, ModelPrice] = {
    # OpenAI
    "gpt-4o-2024-11-20": ModelPrice(0.0025, 0.01),
    "gpt-4o": ModelPrice(0.0025, 0.01),
    "gpt-4o-mini": ModelPrice(0.00015, 0.0006),
    "o3-2025-04-16": ModelPrice(0.060, 0.240),     # placeholder; verify when running
    "o3": ModelPrice(0.060, 0.240),

    # Anthropic (for any cross-VLM comparison)
    "claude-sonnet-4-6": ModelPrice(0.003, 0.015),
    "claude-opus-4-7": ModelPrice(0.015, 0.075),

    # DashScope / SiliconFlow (Qwen)
    "Qwen/Qwen2.5-VL-7B-Instruct": ModelPrice(0.0, 0.0),    # open-source; track GPU time elsewhere
    "Qwen/Qwen2.5-VL-72B-Instruct": ModelPrice(0.0008, 0.0024),  # SiliconFlow public rate
    "Qwen/Qwen2.5-72B-Instruct": ModelPrice(0.0008, 0.0024),
    # Qwen3 VL family — SiliconFlow public rates as of 2026-05.
    "Qwen/Qwen3-VL-32B-Instruct": ModelPrice(0.0006, 0.0018),
    "Qwen/Qwen3-32B": ModelPrice(0.0006, 0.0018),
    "Qwen/Qwen3-8B": ModelPrice(0.0002, 0.0006),
    "Qwen/Qwen3-VL-72B-Instruct": ModelPrice(0.0010, 0.0030),
}


def is_known(model: str) -> bool:
    """True iff ``model`` has an explicit entry in :data:`PRICING`.

    D2 uses this to surface unknown-model identifiers in its output so a
    silently-zero cost in the headline number is detectable in the
    per-cell extra payload."""
    return model in PRICING


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return USD cost for one call. Unknown models charge zero — callers
    that need to flag this should check :func:`is_known` first."""
    p = PRICING.get(model)
    if p is None:
        return 0.0
    return (prompt_tokens / 1000.0) * p.input_per_1k_usd + (completion_tokens / 1000.0) * p.output_per_1k_usd


def lookup(model: str) -> ModelPrice:
    return PRICING.get(model, ModelPrice(0.0, 0.0))
