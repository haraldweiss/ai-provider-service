"""Grading logic for model evaluation responses.

Combines deterministic checks (length, keywords) with LLM-as-judge scoring.
"""

from __future__ import annotations
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

GRADE_LABELS = {
    (4.5, 5.0): 'excellent',
    (3.5, 4.5): 'good',
    (2.5, 3.5): 'acceptable',
    (1.5, 2.5): 'poor',
    (0.0, 1.5): 'very_poor',
}


def score_to_grade(score: float) -> str:
    for (lo, hi), label in GRADE_LABELS.items():
        if lo <= score <= hi:
            return label
    return 'unknown'


def deterministic_score(
    response: str,
    expected_keywords: Optional[str] = None,
    min_length: Optional[int] = None,
    max_length: Optional[int] = None,
) -> float:
    """Score 0.0-1.0 based on deterministic checks."""
    if not response or not response.strip():
        return 0.0

    scores = []
    text = response.strip()
    length = len(text)

    if min_length is not None:
        scores.append(min(1.0, length / max(1, min_length)))

    if max_length is not None:
        if length > max_length:
            scores.append(max(0.0, 1.0 - (length - max_length) / max(1, max_length)))
        else:
            scores.append(1.0)

    if expected_keywords:
        keywords = [k.strip().lower() for k in expected_keywords.split(',') if k.strip()]
        if keywords:
            text_lower = text.lower()
            found = sum(1 for kw in keywords if kw in text_lower)
            scores.append(found / len(keywords))

    if not scores:
        return 0.5

    return sum(scores) / len(scores)


JUDGE_SYSTEM_PROMPT = """You are an impartial evaluator grading AI model responses.
Score the response on two dimensions (each 1-5):

1. ACCURACY: Does the response correctly address the task? Is the information factually correct?
2. QUALITY: Is the response well-structured, clear, complete, and helpful?

Respond with ONLY a JSON object: {"accuracy": <1-5>, "quality": <1-5>, "reason": "<brief explanation>"}
No other text."""


def llm_judge_score(
    task_prompt: str,
    response: str,
    grading_criteria: Optional[str] = None,
    judge_dispatch_fn=None,
) -> dict:
    """Use LLM-as-judge to score a response. Returns {accuracy, quality, reason}.

    judge_dispatch_fn: callable(messages) -> response_text. If None, returns
    a fallback score based on response non-emptiness.
    """
    if not response or not response.strip():
        return {'accuracy': 1.0, 'quality': 1.0, 'reason': 'empty response'}

    user_msg = f"Task: {task_prompt}\n\nResponse to evaluate:\n{response}"
    if grading_criteria:
        user_msg += f"\n\nSpecific criteria:\n{grading_criteria}"

    if judge_dispatch_fn is None:
        return {'accuracy': 3.0, 'quality': 3.0, 'reason': 'no judge configured'}

    try:
        messages = [
            {'role': 'system', 'content': JUDGE_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_msg},
        ]
        judge_response = judge_dispatch_fn(messages)
        match = re.search(r'\{[^}]+\}', judge_response)
        if match:
            data = json.loads(match.group())
            accuracy = max(1.0, min(5.0, float(data.get('accuracy', 3.0))))
            quality = max(1.0, min(5.0, float(data.get('quality', 3.0))))
            return {
                'accuracy': accuracy,
                'quality': quality,
                'reason': str(data.get('reason', ''))[:500],
            }
    except Exception as e:
        logger.warning('LLM judge failed: %s', e)

    return {'accuracy': 3.0, 'quality': 3.0, 'reason': 'judge parse failed'}


def composite_score(
    accuracy: float,
    quality: float,
    deterministic: float,
    latency_ms: Optional[int] = None,
    cost_usd: Optional[float] = None,
) -> float:
    """Weighted composite score 0.0-5.0.

    accuracy (LLM judge, 1-5): 40%
    quality (LLM judge, 1-5): 30%
    deterministic (0-1, scaled to 1-5): 20%
    speed bonus (0-1): 10%
    """
    det_scaled = 1.0 + deterministic * 4.0

    speed_bonus = 0.0
    if latency_ms is not None and latency_ms > 0:
        if latency_ms < 2000:
            speed_bonus = 1.0
        elif latency_ms < 10000:
            speed_bonus = 0.7
        elif latency_ms < 30000:
            speed_bonus = 0.4
        elif latency_ms < 60000:
            speed_bonus = 0.2

    score = (
        accuracy * 0.40
        + quality * 0.30
        + det_scaled * 0.20
        + speed_bonus * 5.0 * 0.10
    )
    return round(max(0.0, min(5.0, score)), 2)
