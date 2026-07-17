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


def strip_leading_h1_markdown(text: str) -> str:
    """Remove the first markdown heading so the Edition H1 is not duplicated in the body."""
    lines = (text or "").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#\s", lines[0]):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()
