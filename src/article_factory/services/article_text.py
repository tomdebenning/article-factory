from __future__ import annotations

import re


def headline_from_markdown(text: str, *, max_words: int = 8) -> str:
    """Return a short headline from article body markdown (first N words)."""
    plain = text or ""
    plain = re.sub(r"```.*?```", " ", plain, flags=re.S)
    plain = re.sub(r"`([^`]+)`", r"\1", plain)
    plain = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", plain)
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
    plain = re.sub(r"^#+\s*", "", plain, flags=re.M)
    plain = re.sub(r"[*_~>|#-]", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    words = plain.split()
    if not words:
        return "Article"
    return " ".join(words[:max_words])


def article_has_content(text: str) -> bool:
    return bool((text or "").strip())
