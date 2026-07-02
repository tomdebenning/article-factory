from article_factory.services.api_keys import is_real_api_key, mask_api_key


def test_mask_api_key_short() -> None:
    assert mask_api_key("abc") == "••••••••"


def test_is_real_api_key_rejects_placeholder() -> None:
    assert is_real_api_key("change-me-admin") is False
    assert is_real_api_key("real-generated-key-value") is True
