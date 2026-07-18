from __future__ import annotations

import pytest

from article_factory.services.cms_connection import check_cms_connection


@pytest.mark.asyncio
async def test_check_cms_connection_falls_back_to_https(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_probe(base: str, cms_api_key: str) -> tuple[bool, str]:
        calls.append(base)
        if base.startswith("https://"):
            return True, f"Connected to Showroom CMS at {base}"
        return False, "Showroom CMS unreachable: Empty reply from server"

    monkeypatch.setattr("article_factory.services.cms_connection._probe_cms", fake_probe)

    ok, message = await check_cms_connection("http://127.0.0.1:8200", "secret")
    assert ok is True
    assert "https://127.0.0.1:8200" in message
    assert calls == ["http://127.0.0.1:8200", "https://127.0.0.1:8200"]


@pytest.mark.asyncio
async def test_alternate_local_cms_url() -> None:
    from article_factory.services.cms_connection import alternate_local_cms_url

    assert alternate_local_cms_url("http://127.0.0.1:8200") == "https://127.0.0.1:8200"
    assert alternate_local_cms_url("https://localhost:8200") == "http://localhost:8200"
    assert alternate_local_cms_url("http://sg02:8200") is None
    ok, message = await check_cms_connection("", "")
    assert ok is False
    assert "Not configured" in message


@pytest.mark.asyncio
async def test_check_cms_connection_missing_api_key() -> None:
    ok, message = await check_cms_connection("http://cms.test:8200", "")
    assert ok is False
    assert "API key" in message


@pytest.mark.asyncio
async def test_check_cms_connection_success(monkeypatch) -> None:
    class FakeCms:
        base_url = "http://cms.test:8200"

        def _headers(self):
            return {"X-API-Key": "secret"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls: list[tuple[str, str]] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            self.calls.append(("GET", url))

            class Resp:
                def raise_for_status(self): ...

            return Resp()

        async def put(self, url, json=None, headers=None):
            self.calls.append(("PUT", url))

            class Resp:
                status_code = 200

                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr("article_factory.services.cms_connection.CmsClient", lambda **kwargs: FakeCms())
    monkeypatch.setattr("article_factory.services.cms_connection.httpx.AsyncClient", FakeClient)

    ok, message = await check_cms_connection("http://cms.test:8200", "secret")
    assert ok is True
    assert "Connected to Showroom CMS" in message


@pytest.mark.asyncio
async def test_check_cms_connection_rejects_api_key(monkeypatch) -> None:
    class FakeCms:
        base_url = "http://cms.test:8200"

        def _headers(self):
            return {"X-API-Key": "bad"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

        async def put(self, url, json=None, headers=None):
            class Resp:
                status_code = 401

                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr("article_factory.services.cms_connection.CmsClient", lambda **kwargs: FakeCms())
    monkeypatch.setattr("article_factory.services.cms_connection.httpx.AsyncClient", FakeClient)

    ok, message = await check_cms_connection("http://cms.test:8200", "bad")
    assert ok is False
    assert "401" in message


@pytest.mark.asyncio
async def test_check_cms_connection_showroom_not_configured(monkeypatch) -> None:
    class FakeCms:
        base_url = "http://cms.test:8200"

        def _headers(self):
            return {"X-API-Key": "secret"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

        async def put(self, url, json=None, headers=None):
            class Resp:
                status_code = 503
                text = "not configured"

                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr("article_factory.services.cms_connection.CmsClient", lambda **kwargs: FakeCms())
    monkeypatch.setattr("article_factory.services.cms_connection.httpx.AsyncClient", FakeClient)

    ok, message = await check_cms_connection("http://cms.test:8200", "secret")
    assert ok is False
    assert "/admin" in message
