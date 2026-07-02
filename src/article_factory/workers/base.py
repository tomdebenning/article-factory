from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class StepContext:
    step_key: str
    label: str
    system_prompt: str
    user_prompt_template: str
    puller: str
    model: str
    variables: dict[str, str] = field(default_factory=dict)
    enabled_tools: dict[str, bool] | None = None
    run_id: str = ""
    brave_search_api_key: str = ""


def render_prompt(template: str, variables: dict[str, str]) -> str:
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


def current_datetime_line(*, now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = moment.strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"Current date and time: {stamp}"


def prepend_current_datetime(system_prompt: str, *, now: datetime | None = None) -> str:
    line = current_datetime_line(now=now)
    body = system_prompt.strip()
    if not body:
        return line
    return f"{line}\n\n{body}"


def review_accepted(content: str) -> bool:
    from article_factory.services.verdict import Verdict, parse_verdict

    return parse_verdict(content) == Verdict.ACCEPT


def review_feedback(content: str) -> str:
    from article_factory.services.verdict import extract_feedback_body

    return extract_feedback_body(content)
