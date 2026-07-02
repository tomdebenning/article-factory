from __future__ import annotations

import secrets

PLACEHOLDER_API_KEYS = frozenset({"change-me-admin", "change-me-factory-to-cms"})


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def normalize_api_key(value: str | None) -> str:
    return (value or "").strip()


def is_real_api_key(value: str | None) -> bool:
    normalized = normalize_api_key(value)
    return bool(normalized) and normalized not in PLACEHOLDER_API_KEYS


def mask_api_key(value: str) -> str:
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:4]}…{value[-4:]}"
