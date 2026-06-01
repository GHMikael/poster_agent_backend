"""Shared OpenAI-compatible client for the SiliconFlow / DashScope-backed
judges (A1 / A2 / A3 / C1).

* ``text_chat`` — JSON-mode text-only call (used by claim_extractor,
  nli_judge.binary_entailment, paperquiz_generator).
* ``vlm_chat`` — image + text call (used by altclip_judge replacement,
  paperquiz_answerer).
* Both have transparent SHA256 caching under
  ``experiments/.cache/llm/{text,vlm}/<hash>.json`` so re-running A1/A3
  across the matrix costs zero LLM dollars after the first pass.

Why not raw ``openai.OpenAI`` everywhere: judges otherwise each redo the
same dotenv load + cache layout + token logging, which is brittle and
duplicates work. Centralised here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI


# Match app/config.py — make DASHSCOPE_API_KEY visible to judges spawned
# in subprocesses that never imported app.config.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


_DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
_DEFAULT_API_KEY_ENV = "DASHSCOPE_API_KEY"
_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_MAX_RETRIES = 3
_CACHE_ROOT = Path("experiments/.cache/llm")


def _hash_key(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:24]


def _client(api_key_env: str, base_url: Optional[str]) -> OpenAI:
    timeout_s = float(os.getenv("POSTER_LLM_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S)))
    return OpenAI(
        api_key=os.getenv(api_key_env, ""),
        base_url=base_url or _DEFAULT_BASE_URL,
        timeout=timeout_s,
        max_retries=0,
    )


def _max_retries() -> int:
    return max(1, int(os.getenv("POSTER_LLM_MAX_RETRIES", str(_DEFAULT_MAX_RETRIES))))


def text_chat(
    *,
    system: str,
    user: str,
    model: str,
    temperature: float = 0.0,
    json_mode: bool = True,
    api_key_env: str = _DEFAULT_API_KEY_ENV,
    base_url: Optional[str] = None,
    cache_subdir: str = "text",
    experiment_logger: Optional[Any] = None,
    stage_label: str = "text_chat",
) -> Dict[str, Any]:
    """OpenAI-compatible chat with on-disk caching. Returns ``{"content": str, "usage": {...}, "cache_hit": bool}``.

    With ``json_mode=True`` the caller is responsible for parsing
    ``content`` — we never silently swallow JSON errors here.
    """
    cache_dir = _CACHE_ROOT / cache_subdir
    key = _hash_key(model, str(temperature), system, user, str(json_mode))
    cache_path = cache_dir / f"{key}.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            data["cache_hit"] = True
            if experiment_logger is not None:
                experiment_logger.log_llm_call(
                    stage=stage_label,
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0.0,
                    raw_response=None,
                    retries=0,
                    extra={"cache_hit": True},
                )
            return data
        except Exception:
            pass

    client = _client(api_key_env, base_url)
    t0 = time.perf_counter()
    kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    # Retry on transient connection errors. SiliconFlow occasionally drops
    # connections under heavy load; 3 tries with exponential backoff covers
    # the typical case without masking a real outage.
    resp = None
    max_retries = _max_retries()
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    latency_ms = (time.perf_counter() - t0) * 1000

    content = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    p_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
    c_tok = int(getattr(usage, "completion_tokens", 0) or 0)

    out = {
        "content": content,
        "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok},
        "cache_hit": False,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    if experiment_logger is not None:
        try:
            experiment_logger.log_llm_call(
                stage=stage_label,
                model=model,
                prompt_tokens=p_tok,
                completion_tokens=c_tok,
                latency_ms=latency_ms,
                raw_response=None,
                retries=0,
                extra={"cache_hit": False},
            )
        except Exception:
            pass
    return out


def vlm_chat(
    *,
    system: str,
    user: str,
    image_paths: List[Path],
    model: str,
    temperature: float = 0.0,
    json_mode: bool = True,
    api_key_env: str = _DEFAULT_API_KEY_ENV,
    base_url: Optional[str] = None,
    experiment_logger: Optional[Any] = None,
    stage_label: str = "vlm_chat",
) -> Dict[str, Any]:
    """VLM chat: text + 1+ images. SiliconFlow expects base64 data URLs."""
    # Cache key by image bytes hash + prompt
    parts: List[str] = [model, str(temperature), system, user, str(json_mode)]
    image_blobs: List[bytes] = []
    for p in image_paths:
        b = p.read_bytes()
        image_blobs.append(b)
        parts.append(hashlib.sha256(b).hexdigest()[:16])

    cache_dir = _CACHE_ROOT / "vlm"
    key = _hash_key(*parts)
    cache_path = cache_dir / f"{key}.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            data["cache_hit"] = True
            if experiment_logger is not None:
                experiment_logger.log_llm_call(
                    stage=stage_label,
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0.0,
                    raw_response=None,
                    retries=0,
                    extra={"cache_hit": True, "n_images": len(image_paths)},
                )
            return data
        except Exception:
            pass

    content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user}]
    for blob in image_blobs:
        b64 = base64.b64encode(blob).decode("ascii")
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    client = _client(api_key_env, base_url)
    t0 = time.perf_counter()
    kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content_parts},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = None
    max_retries = _max_retries()
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    latency_ms = (time.perf_counter() - t0) * 1000

    content = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    p_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
    c_tok = int(getattr(usage, "completion_tokens", 0) or 0)

    out = {
        "content": content,
        "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok},
        "cache_hit": False,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    if experiment_logger is not None:
        try:
            experiment_logger.log_llm_call(
                stage=stage_label,
                model=model,
                prompt_tokens=p_tok,
                completion_tokens=c_tok,
                latency_ms=latency_ms,
                raw_response=None,
                retries=0,
                extra={"cache_hit": False, "n_images": len(image_paths)},
            )
        except Exception:
            pass
    return out


def parse_json(content: str) -> Dict[str, Any]:
    """Strict JSON parse with a single recovery attempt for the common
    `json + trailing prose` case some models emit."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise ValueError(f"could not parse JSON; first 200 chars: {content[:200]!r}")
