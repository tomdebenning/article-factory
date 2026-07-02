from __future__ import annotations

from article_factory.services.article_text import article_has_content, headline_from_markdown


def test_headline_from_markdown_uses_first_eight_words() -> None:
    body = (
        "Cats are independent yet affectionate companions cherished around the globe. "
        "Their soft fur and melodic purrs provide comfort."
    )
    assert headline_from_markdown(body) == (
        "Cats are independent yet affectionate companions cherished around"
    )


def test_headline_from_markdown_strips_markdown() -> None:
    body = "# Big Win\n\nGreat game last night under the lights."
    assert headline_from_markdown(body) == "Big Win Great game last night under the"


def test_article_has_content() -> None:
    assert article_has_content("hello")
    assert not article_has_content("")
    assert not article_has_content("   \n  ")
