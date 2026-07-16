from __future__ import annotations

import pytest

from article_factory.services.factory_readiness import assess_factory_readiness
from article_factory.services.runtime_settings import RuntimeSettings


async def _cms_ok(*args, **kwargs):
    return True, "Connected to Showroom CMS at http://cms.test:8200"


@pytest.mark.asyncio
async def test_readiness_reminds_brave_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        _cms_ok,
    )

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    async def fake_get(*args, **kwargs):
        class Resp:
            def raise_for_status(self):
                return None

        return Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="secret",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 1, "running": 0, "completed": 0, "failed": 0},
    )
    brave = next(c for c in result["checks"] if c["id"] == "brave_search")
    assert brave["ok"] is False
    assert any(c["id"] == "brave_search" for c in result["issue_checks"])


@pytest.mark.asyncio
async def test_readiness_flags_missing_brave_when_flow_needs_search(monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        _cms_ok,
    )
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.collect_flow_tool_requirements",
        lambda: {"needs_write_file": False, "needs_web_search": True},
    )

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    async def fake_get(*args, **kwargs):
        class Resp:
            def raise_for_status(self):
                return None

        return Resp()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="secret",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    brave = next(c for c in result["checks"] if c["id"] == "brave_search")
    assert brave["ok"] is False
    assert "brave_search" in result.get("issue_checks", []) or any(
        c["id"] == "brave_search" for c in result["issue_checks"]
    )


@pytest.mark.asyncio
async def test_readiness_setup_required_without_model() -> None:
    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="",
            cms_api_key="",
            default_puller="",
            default_model="",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "setup_required"
    assert result["setup_complete"] is False
    assert any(c["id"] == "model" and not c["ok"] for c in result["checks"])
    assert any(c["id"] == "cms_url" and not c["ok"] for c in result["checks"])


@pytest.mark.asyncio
async def test_readiness_setup_required_without_showroom(monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        _cms_ok,
    )

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="",
            cms_api_key="",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="brave-key",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "needs_topics"
    assert result["setup_complete"] is True
    assert result["can_publish"] is False
    cms = next(c for c in result["checks"] if c["id"] == "cms_url")
    assert cms["ok"] is False


@pytest.mark.asyncio
async def test_readiness_needs_topics_when_setup_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        _cms_ok,
    )
    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="secret",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="brave-key",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "needs_topics"
    assert result["setup_complete"] is True
    topics = next(c for c in result["checks"] if c["id"] == "topics")
    assert topics["ok"] is False


@pytest.mark.asyncio
async def test_readiness_ready_with_queued_topics(monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        _cms_ok,
    )
    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="secret",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="brave-key",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 2, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "ready"
    assert result["can_write"] is True


@pytest.mark.asyncio
async def test_readiness_showroom_connection_failed(monkeypatch) -> None:
    async def cms_fail(*args, **kwargs):
        return False, "Showroom CMS unreachable: connection refused"

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        cms_fail,
    )

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="secret",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="brave-key",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "needs_topics"
    assert result["setup_complete"] is True
    assert result["can_publish"] is False
    cms = next(c for c in result["checks"] if c["id"] == "cms_connection")
    assert cms["ok"] is False
    assert cms["action_path"] == "/settings"
    assert cms["id"] not in {c["id"] for c in result["issue_checks"]}


@pytest.mark.asyncio
async def test_readiness_can_write_when_showroom_down_but_control_plane_ok(monkeypatch) -> None:
    async def cms_fail(*args, **kwargs):
        return False, "Showroom CMS unreachable: connection refused"

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        cms_fail,
    )

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="secret",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="brave-key",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 2, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["setup_complete"] is True
    assert result["can_write"] is True
    assert result["can_publish"] is False
    assert result["phase"] == "ready"


@pytest.mark.asyncio
async def test_readiness_processing_phase() -> None:
    from article_factory.models import FactoryRun

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="",
            cms_url="",
            cms_api_key="",
            default_puller="",
            default_model="",
        ),
        loop_running=True,
        active_run=FactoryRun(run_id="run-x", topic_slug="sports", status="running"),
        queue_counts={"queued": 0, "running": 1, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "processing"


@pytest.mark.asyncio
async def test_readiness_parallel_processing_headline(monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        _cms_ok,
    )

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="key",
            default_puller="",
            default_model="llama3",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 2, "completed": 0, "failed": 0},
        active_run_count=2,
    )
    assert result["phase"] == "processing"
    assert "2 articles" in result["headline"]


@pytest.mark.asyncio
async def test_readiness_issue_checks_omit_passing_setup(monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.check_cms_connection",
        _cms_ok,
    )

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="http://cms.test:8200",
            cms_api_key="key",
            default_puller="",
            default_model="llama3",
            brave_search_api_key="brave-key",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["issue_checks"] == []
    topics = next(c for c in result["checks"] if c["id"] == "topics")
    assert topics["ok"] is False


@pytest.mark.asyncio
async def test_readiness_orchestrator_stopped() -> None:
    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="",
            cms_api_key="",
            default_puller="",
            default_model="llama3",
        ),
        loop_running=False,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "setup_required"
    orchestrator = next(c for c in result["checks"] if c["id"] == "orchestrator")
    assert orchestrator["ok"] is False


@pytest.mark.asyncio
async def test_readiness_control_plane_unreachable() -> None:
    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="",
            cms_api_key="",
            default_puller="",
            default_model="llama3",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    assert result["phase"] == "setup_required"
    cp = next(c for c in result["checks"] if c["id"] == "control_plane_reachable")
    assert cp["ok"] is False


@pytest.mark.asyncio
async def test_readiness_no_pullers_for_model(monkeypatch) -> None:
    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["other-model"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="",
            cms_api_key="",
            default_puller="",
            default_model="llama3",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    pullers = next(c for c in result["checks"] if c["id"] == "pullers")
    assert pullers["ok"] is False


@pytest.mark.asyncio
async def test_readiness_active_puller_not_idle(monkeypatch) -> None:
    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-busy",
                    "is_active": True,
                    "is_stale": False,
                    "status": "busy",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    result = await assess_factory_readiness(
        runtime=RuntimeSettings(
            control_plane_url="http://cp.test:8000",
            cms_url="",
            cms_api_key="",
            default_puller="",
            default_model="llama3",
        ),
        loop_running=True,
        active_run=None,
        queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
    )
    pullers = next(c for c in result["checks"] if c["id"] == "pullers")
    assert pullers["ok"] is True
    assert "none idle" in pullers["message"]



