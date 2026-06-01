"""VLM Commenter & SVFP (Structured Visual Feedback Protocol).

This module provides two complementary capabilities for the visual feedback
loop of the poster agent:

1. ``check_layout_with_vlm``: an OpenAI-compatible call that asks a
   vision-language model (Qwen-VL by default) to inspect a poster preview
   image and return a structured JSON critique. Kept for backwards
   compatibility with :mod:`app.feedback_loop`.

2. The **SVFP** layer (``SVFPIssueType``, :func:`build_feedback`,
   :func:`build_feedback_batch`, :func:`get_svfp_schema`,
   :func:`validate_feedback`): a small, dependency-free structured protocol
   for emitting/validating individual feedback items. Each feedback item is a
   dictionary with the keys ``issue_type`` / ``details`` / ``suggested_fix``
   and follows the JSON Schema returned by :func:`get_svfp_schema`.

The SVFP layer is what downstream agents (Dify / closed-loop scripts /
frontend) consume. It is intentionally independent of the VLM transport so
that heuristic checkers, human reviewers, or other models can produce
identical payloads.
"""

from __future__ import annotations

import base64
import io
import json
import os
import signal
import threading
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from openai import OpenAI
from PIL import Image

from app.config import DASHSCOPE_API_KEY, QWEN_VL_MODEL


# ---------------------------------------------------------------------------
# SVFP: Structured Visual Feedback Protocol
# ---------------------------------------------------------------------------


class SVFPIssueType(str, Enum):
    """Enumeration of visual issue types recognised by the SVFP layer.

    Trimmed from the original 10-class taxonomy down to the **two root-cause
    issues** (overlapping_elements + empty_space) that account for almost all
    visual defects in auto-generated posters, plus **low_contrast** as a
    standalone palette-level issue. Removed classes either had no actionable
    fix in the current architecture, were redundant with these three, or
    didn't reliably surface in the renderer's output.

    Inheriting from ``str`` lets each member be JSON-serialised directly and
    compared against raw strings coming from the VLM.
    """

    OVERLAPPING_ELEMENTS = "overlapping_elements"
    EMPTY_SPACE = "empty_space"
    LOW_CONTRAST = "low_contrast"
    FIGURE_TOO_SMALL = "figure_too_small"


SVFP_ISSUE_VALUES: List[str] = [item.value for item in SVFPIssueType]


class SVFPSuggestedAction(str, Enum):
    """The closed set of remediations the FeedbackApplier knows how to perform.

    Each action maps to a single concrete mutation on the ``PosterTask``:

    Overlapping fixes:
        * ``reduce_bullet_count`` — drop bullets to a target count (default 4)
        * ``shrink_text`` — multiply ``panel.body_font_scale`` by 0.85
        * ``truncate_bullets`` — clamp each bullet to 80 chars
        * ``shrink_figure_box`` — switch ``layout_hint`` to ``image_compact``

    Empty-space fixes:
        * ``enlarge_font`` — multiply ``panel.body_font_scale`` by 1.15
        * ``add_bullet`` — append one supporting bullet
        * ``compact_figure_box`` — switch ``layout_hint`` to ``image_compact``
          (figure shrinks, text fills the freed area)

    Contrast fix:
        * ``switch_palette`` — rotate ``task.color_theme`` to the next
          high-contrast palette (academic_blue ↔ engineering_green)

    Sentinel:
        * ``none`` — VLM saw no actionable problem
    """

    # overlapping fixes
    REDUCE_BULLET_COUNT = "reduce_bullet_count"
    SHRINK_TEXT = "shrink_text"
    TRUNCATE_BULLETS = "truncate_bullets"
    SHRINK_FIGURE_BOX = "shrink_figure_box"
    # empty_space fixes
    ENLARGE_FONT = "enlarge_font"
    ADD_BULLET = "add_bullet"
    COMPACT_FIGURE_BOX = "compact_figure_box"
    # low_contrast fix
    SWITCH_PALETTE = "switch_palette"
    # sentinel
    NONE = "none"


SVFP_SUGGESTED_ACTION_VALUES: List[str] = [a.value for a in SVFPSuggestedAction]


# Default suggested-fix mapping. Used when callers do not provide an explicit
# ``suggested_fix``. Each root-cause issue maps to its most common remediation;
# the applier can still override based on context.
_DEFAULT_FIXES: Dict[str, str] = {
    SVFPIssueType.OVERLAPPING_ELEMENTS.value: SVFPSuggestedAction.REDUCE_BULLET_COUNT.value,
    SVFPIssueType.EMPTY_SPACE.value: SVFPSuggestedAction.ENLARGE_FONT.value,
    SVFPIssueType.LOW_CONTRAST.value: SVFPSuggestedAction.SWITCH_PALETTE.value,
    SVFPIssueType.FIGURE_TOO_SMALL.value: SVFPSuggestedAction.REDUCE_BULLET_COUNT.value,
}


def _coerce_issue_type(issue_type: Any) -> str:
    """Normalise an issue identifier into a known SVFP value.

    Accepts :class:`SVFPIssueType`, raw strings, or anything ``str``-able.
    Raises ``ValueError`` if the identifier does not match an SVFP issue.
    """

    if isinstance(issue_type, SVFPIssueType):
        return issue_type.value
    text = str(issue_type).strip().lower()
    if text not in SVFP_ISSUE_VALUES:
        raise ValueError(
            f"Unknown SVFP issue type: {issue_type!r}. "
            f"Expected one of {SVFP_ISSUE_VALUES}"
        )
    return text


def build_feedback(
    issue_type: Any,
    details: str,
    suggested_fix: Optional[str] = None,
    section: Optional[str] = None,
    severity: Optional[str] = None,
    target_value: Optional[Any] = None,
) -> Dict[str, Any]:
    """Construct a single SVFP feedback item as a plain ``dict``.

    Parameters
    ----------
    issue_type:
        One of :class:`SVFPIssueType` (or its string value).
    details:
        Human-readable description of what is wrong with the layout.
    suggested_fix:
        Concrete action the renderer / applier should perform. When omitted,
        a sensible default is picked from ``_DEFAULT_FIXES``.
    section:
        Optional panel/section identifier this issue belongs to.
    severity:
        Optional severity level. Must be ``"low"`` / ``"medium"`` / ``"high"``
        when provided.
    target_value:
        Optional numeric or string hint consumed by the FeedbackApplier
        (e.g. desired bullet count or layout hint).

    Returns
    -------
    dict
        A JSON-ready feedback record with at least the keys ``issue_type``,
        ``details`` and ``suggested_fix``.
    """

    normalised = _coerce_issue_type(issue_type)
    fix = suggested_fix or _DEFAULT_FIXES.get(normalised, "none")
    if severity is not None and severity not in {"low", "medium", "high"}:
        raise ValueError("severity must be one of 'low', 'medium', 'high'")

    payload: Dict[str, Any] = {
        "issue_type": normalised,
        "details": str(details),
        "suggested_fix": str(fix),
    }
    if section is not None:
        payload["section"] = str(section)
    if severity is not None:
        payload["severity"] = severity
    if target_value is not None:
        payload["target_value"] = target_value
    return payload


def build_feedback_batch(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build a list of SVFP feedback dicts from a list of kwargs dicts.

    Each element of ``items`` is forwarded as keyword arguments to
    :func:`build_feedback`. Convenient for translating heuristic checker
    output into SVFP form.
    """

    return [build_feedback(**item) for item in items]


def feedback_to_json(feedback: Dict[str, Any], indent: int = 2) -> str:
    """Serialise an SVFP feedback dict to a UTF-8 JSON string."""

    validate_feedback(feedback)
    return json.dumps(feedback, ensure_ascii=False, indent=indent)


def get_svfp_schema() -> Dict[str, Any]:
    """Return the JSON Schema (draft-07) describing an SVFP feedback item.

    Frontends and closed-loop validators can use this to ensure a feedback
    record produced by any source (VLM, heuristic, human) conforms to the
    same protocol.
    """

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SVFPFeedback",
        "description": "Structured Visual Feedback Protocol single record.",
        "type": "object",
        "additionalProperties": False,
        "required": ["issue_type", "details", "suggested_fix"],
        "properties": {
            "issue_type": {
                "type": "string",
                "enum": SVFP_ISSUE_VALUES,
                "description": "Canonical visual issue label.",
            },
            "details": {
                "type": "string",
                "minLength": 1,
                "description": "Natural-language explanation of the issue.",
            },
            "suggested_fix": {
                "type": "string",
                "enum": SVFP_SUGGESTED_ACTION_VALUES,
                "description": "Concrete repair action for the FeedbackApplier (closed enum).",
            },
            "section": {
                "type": "string",
                "description": "Panel/section name (optional).",
            },
            "severity": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Issue severity (optional).",
            },
            "target_value": {
                "description": "Optional target hint (number or string).",
            },
        },
    }


def get_svfp_batch_schema() -> Dict[str, Any]:
    """Return the JSON Schema for a list of SVFP feedback items."""

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SVFPFeedbackBatch",
        "type": "array",
        "items": get_svfp_schema(),
    }


def validate_feedback(feedback: Any) -> None:
    """Lightweight structural validator for a single SVFP feedback dict.

    Avoids pulling in a full JSON Schema library: checks required fields,
    canonical issue type, and severity enum. Accepts ``Any`` so that callers
    passing wrong types still hit the runtime check rather than silently
    succeeding under a permissive type-checker.

    Raises
    ------
    ValueError
        If the payload is malformed.
    """

    if not isinstance(feedback, dict):
        raise ValueError("Feedback must be a dict.")
    for key in ("issue_type", "details", "suggested_fix"):
        if key not in feedback:
            raise ValueError(f"Missing required field: {key}")
        if not isinstance(feedback[key], str) or not feedback[key].strip():
            raise ValueError(f"Field '{key}' must be a non-empty string.")
    if feedback["issue_type"] not in SVFP_ISSUE_VALUES:
        raise ValueError(
            f"issue_type must be one of {SVFP_ISSUE_VALUES}, got {feedback['issue_type']!r}"
        )
    if feedback["suggested_fix"] not in SVFP_SUGGESTED_ACTION_VALUES:
        raise ValueError(
            f"suggested_fix must be one of {SVFP_SUGGESTED_ACTION_VALUES}, "
            f"got {feedback['suggested_fix']!r}"
        )
    severity = feedback.get("severity")
    if severity is not None and severity not in {"low", "medium", "high"}:
        raise ValueError("severity must be one of 'low', 'medium', 'high'")


# ---------------------------------------------------------------------------
# Legacy VLM transport (kept for compatibility with VisualFeedbackLoop)
# ---------------------------------------------------------------------------


def image_to_base64(image: Image.Image) -> str:
    """Encode a Pillow image as a base64 PNG string."""

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract a JSON object from raw VLM text.

    Strategy (in order):

    1. Try to ``json.loads`` the whole string.
    2. Slice from the first ``{`` to the last ``}`` and re-parse.
    3. If the response was truncated mid-string, walk back to the last
       balanced ``}`` and try again — recovers a partial-but-valid prefix.

    Returns ``None`` if no recoverable JSON can be found.
    """

    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    try:
        start = text.index("{")
    except ValueError:
        return None

    # Greedy: try the largest balanced slice we can find.
    depth = 0
    last_close = -1
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_close = i
                break

    if last_close > start:
        candidate = text[start : last_close + 1]
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else None
        except Exception:
            pass

    # Truncation recovery: try progressively smaller slices that end at a
    # ``}`` we can see in the raw text.
    closes = [i for i, ch in enumerate(text) if ch == "}" and i > start]
    for end in reversed(closes):
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            continue

    return None


def check_layout_with_vlm(
    image: Image.Image,
    *,
    experiment_logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ask the configured VLM to review a poster preview image.

    Returns a dict that already aligns with the layout-feedback schema used
    by :class:`app.feedback_loop.VisualFeedbackLoop`. If no API key is set or
    the network call fails, a neutral placeholder is returned so the loop
    can fall back to heuristics.

    When ``experiment_logger`` is non-None, a single ``llm_call`` event is
    emitted with token counts (from ``resp.usage``), latency, and the
    parsed VLM response. Production callers leave the kwarg unset, in
    which case the logger calls are gated by a single ``is not None`` check.

    The function is defensive about VLM transport quirks:

    * ``response_format={"type": "json_object"}`` is requested to nudge the
      model toward valid JSON.
    * ``max_tokens`` is sized for a 6-panel critique (≈1500 tokens).
    * If the model still returns a truncated payload, the multi-level
      :func:`_extract_json_block` recovery is used so partial output is
      still consumable downstream (``source = "vlm_partial"``).
    """

    if not DASHSCOPE_API_KEY:
        return {
            "score": 7.0,
            "global_issues": [],
            "panel_feedback": [],
            "comment": "VLM commenter disabled because DASHSCOPE_API_KEY is empty.",
            "source": "disabled",
        }

    prompt = """
你是一个严格的学术海报视觉缺陷审查助手。

请只检测以下 4 类问题（不要报告其他类型）：

1. overlapping_elements —— 元素重叠：文字超出 panel 边界、文字与图片/caption 像素重合、bullet 之间挤压、跨 panel 内容侵入。
2. empty_space —— 空白过多 / 空间分配不均：panel 内 bullet 太少底部留白、图片在 panel 内占小区域周围 padding 过大、整张海报留白多。
3. low_contrast —— 文字与背景对比不足或 panel 间缺少颜色区分。
4. figure_too_small —— 图片被压扁：panel 是文字图片上下布局且图片被压成又扁又窄的一条 / 图片实际占用面积明显小于其分配的容器，文字 bullet 挤占了图片应有的高度。注意区别于 empty_space —— figure_too_small 的特征是图片本身被letterbox 压缩，而不是 padding 大。

必须只输出合法 JSON，不要输出 Markdown，不要解释，不要在 JSON 之外加任何文字。
panel_feedback 中的 issues 字段只能从以下集合中取值：
["overlapping_elements","empty_space","low_contrast","figure_too_small"]

suggested_action 必须是以下 9 个之一（按检测到的 issue 选择最匹配的 fix）：
- 对 overlapping_elements:
  * reduce_bullet_count —— bullet 数量过多导致溢出
  * shrink_text —— 整体字号偏大导致挤压（缩小该 panel 字号）
  * truncate_bullets —— 单条 bullet 文字太长
  * shrink_figure_box —— 图片占空间过大挤压文字
- 对 empty_space:
  * enlarge_font —— 字号偏小导致留白（放大该 panel 字号）
  * add_bullet —— bullet 太少需要补充内容
  * compact_figure_box —— 图片周围 padding 过大，缩小图片容器让文字占更多空间
- 对 low_contrast:
  * switch_palette —— 切换配色
- 对 figure_too_small:
  * reduce_bullet_count —— 砍掉次要 bullet，腾出垂直空间让图片更大；target_value 建议为 1（只保留最重要那条 bullet）
- none —— 该 panel 无需修改

target_value 含义（数字）：
- reduce_bullet_count: 目标 bullet 条数（figure_too_small 场景下 1；其他场景 3-5）
- truncate_bullets: 目标字符上限（一般 80）
- shrink_text / enlarge_font: 字号缩放系数（如 0.85 / 1.15）
- 其他 action 可省略 target_value

JSON schema:
{
  "score": 1到10之间的数字,
  "global_issues": ["..."],
  "panel_feedback": [
    {"section": "...", "issues": ["..."], "suggested_action": "...", "target_value": 4}
  ],
  "comment": "一句话总结主要问题与改进建议，不能为空"
}
如果布局良好，global_issues 与 panel_feedback 可以为空数组，但 comment 仍需说明。
""".strip()

    try:
        timeout_s = float(os.getenv("POSTER_LLM_TIMEOUT_S", "60"))
        client = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url="https://api.siliconflow.cn/v1",
            timeout=timeout_s,
            max_retries=0,
        )
        kwargs: Dict[str, Any] = dict(
            model=QWEN_VL_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_to_base64(image)}"}},
                    ],
                }
            ],
            temperature=0.1,
            max_tokens=1800,
        )
        # Some OpenAI-compatible endpoints accept response_format; others
        # reject it. In experiment mode the fallback is disabled by default
        # because a second long VLM request can dominate D1 latency.
        import time as _time

        _t0 = _time.perf_counter()
        retries = 0
        strict_json = os.getenv("POSTER_VLM_JSON_MODE", "1") != "0"
        allow_fallback = os.getenv("POSTER_VLM_ALLOW_FALLBACK", "0") == "1"
        wall_timeout_s = float(os.getenv("POSTER_VLM_WALL_TIMEOUT_S", "0"))
        alarm_enabled = (
            wall_timeout_s > 0
            and hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread()
        )
        previous_handler = None
        if alarm_enabled:
            def _raise_timeout(_signum: int, _frame: Any) -> None:
                raise TimeoutError(f"VLM wall timeout after {wall_timeout_s:.1f}s")

            previous_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, _raise_timeout)
            signal.setitimer(signal.ITIMER_REAL, wall_timeout_s)
        try:
            if strict_json:
                resp = client.chat.completions.create(
                    response_format={"type": "json_object"},
                    **kwargs,
                )
            else:
                resp = client.chat.completions.create(**kwargs)
        except Exception:
            if not allow_fallback or not strict_json:
                raise
            retries = 1
            resp = client.chat.completions.create(**kwargs)
        finally:
            if alarm_enabled:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
        _latency_ms = (_time.perf_counter() - _t0) * 1000

        content = (resp.choices[0].message.content or "").strip()
        data = _extract_json_block(content)

        # Telemetry: emit a single llm_call event with token counts and the
        # parsed response. Gated by an explicit None-check so production
        # cost is zero.
        if experiment_logger is not None:
            usage = getattr(resp, "usage", None)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            try:
                experiment_logger.log_llm_call(
                    stage="vlm_layout_review",
                    model=QWEN_VL_MODEL,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=_latency_ms,
                    raw_response={"content": content[:4000], "parsed": data},
                    retries=retries,
                )
            except Exception:
                pass

        if data is not None:
            # Detect "looks complete" vs "recovered partial" so downstream
            # callers know whether to trust the score fully.
            has_score = "score" in data
            has_comment = bool(str(data.get("comment", "")).strip())
            source = "vlm" if (has_score and has_comment) else "vlm_partial"
            data["source"] = source
            return data
        return {
            "score": 7.0,
            "global_issues": [],
            "panel_feedback": [],
            "comment": content[:400] or "VLM returned empty content.",
            "source": "vlm_unparsed",
        }
    except Exception as exc:
        return {"score": 7.0, "global_issues": [], "panel_feedback": [], "comment": f"VLM failed: {exc}", "source": "vlm_error"}


# A standalone demo previously lived here; it is now at
# ``experiments/scratch/vlm_commenter_demo.py``.
