from __future__ import annotations

import re
from enum import Enum

VERDICT_PATTERN = re.compile(
    r"^\s*(?:[\*_]{1,2})?\s*VERDICT\s*:\s*(ACCEPT|REJECT)\s*(?:[\*_]{1,2})?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class Verdict(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    NONE = "none"


def parse_verdict(content: str) -> Verdict:
    matches = list(VERDICT_PATTERN.finditer(content or ""))
    if not matches:
        return Verdict.NONE
    token = matches[-1].group(1).upper()
    return Verdict.ACCEPT if token == "ACCEPT" else Verdict.REJECT


def extract_feedback_body(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    while lines and VERDICT_PATTERN.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()
