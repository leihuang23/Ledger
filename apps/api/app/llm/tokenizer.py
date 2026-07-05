from __future__ import annotations

import re


def count_tokens(text: str, model: str | None = None) -> int:
    """Estimate token count for a string.

    Uses tiktoken when available; otherwise falls back to a deterministic
    heuristic calibrated for English prose (~4 characters per token).
    """
    try:
        import tiktoken

        encoding_name = _encoding_name_for_model(model)
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(text))
    except Exception:
        return _heuristic_token_count(text)


def _encoding_name_for_model(model: str | None) -> str:
    if model is None:
        return "o200k_base"
    model_lower = model.lower()
    if "gpt-4o" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return "o200k_base"
    if "gpt-4" in model_lower or "gpt-3.5-turbo" in model_lower:
        return "cl100k_base"
    return "o200k_base"


def _heuristic_token_count(text: str) -> int:
    if not text:
        return 0
    # Count words and punctuation chunks; calibrate so short English sentences
    # approximate OpenAI token counts (roughly 0.75 words per token).
    tokens = re.findall(r"[\w']+|[.,!?;:\-–—()\[\]{}/\\\"'@#$%&*+=_|<>]", text)
    return max(1, int(len(tokens) / 0.75))
