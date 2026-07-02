from __future__ import annotations

from article_factory.config import Settings


def test_cors_origin_list_explicit() -> None:
    settings = Settings(cors_origins="http://a.test,http://b.test")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]
