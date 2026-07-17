from __future__ import annotations

from datetime import datetime, timezone

import pytest

from article_factory.services.article_text import strip_leading_h1_markdown


def test_strip_leading_h1_markdown() -> None:
    body = "# Big Game Recap\n\nThe team won."
    assert strip_leading_h1_markdown(body) == "The team won."
    assert strip_leading_h1_markdown("No heading here") == "No heading here"
    assert strip_leading_h1_markdown("\n\n## Sub only\n\nText") == "## Sub only\n\nText"


@pytest.mark.asyncio
async def test_generate_edition_headline_fallback(monkeypatch) -> None:
    from article_factory.services.headline_generator import generate_edition_headline

    class Run:
        selected_model = ""
        selected_puller = ""

    draft = "# Local Team Wins Championship In Overtime Thriller"
    headline = await generate_edition_headline(None, draft=draft, run=Run())  # type: ignore[arg-type]
    assert "Local Team Wins" in headline
