from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from article_factory.services.puller_selection import (
    is_idle_puller,
    is_registered_puller,
    pick_puller,
    puller_supports_model,
)


def test_puller_supports_model() -> None:
    puller = {"supported_models": ["llama3", "mistral"]}
    assert puller_supports_model(puller, "llama3") is True
    assert puller_supports_model(puller, "gpt") is False
    assert puller_supports_model({"supported_models": []}, "anything") is True


def test_is_idle_puller() -> None:
    assert is_idle_puller({"is_active": True, "is_stale": False, "status": "ok"}) is True
    assert is_idle_puller({"is_active": False, "is_stale": False, "status": "ok"}) is False
    assert is_idle_puller({"is_active": True, "is_stale": True, "status": "ok"}) is False
    assert is_idle_puller({"is_active": True, "is_stale": False, "status": "busy"}) is False


def test_is_registered_puller() -> None:
    assert is_registered_puller({"is_active": True, "is_stale": False, "status": "busy"}) is True
    assert is_registered_puller({"is_active": True, "is_stale": False, "status": "ok"}) is True
    assert is_registered_puller({"is_active": True, "is_stale": False, "status": "offline"}) is False


def test_pick_puller_prefers_idle() -> None:
    pullers = [
        {"puller_name": "busy-gpu", "is_active": True, "is_stale": False, "status": "busy", "supported_models": ["llama3"]},
        {"puller_name": "gpu-02", "is_active": True, "is_stale": False, "status": "ok", "supported_models": ["llama3"]},
        {"puller_name": "gpu-01", "is_active": True, "is_stale": False, "status": "ok", "supported_models": ["llama3"]},
    ]
    assert pick_puller(pullers, "llama3") == "gpu-01"


def test_pick_puller_requires_model() -> None:
    with pytest.raises(RuntimeError, match="No model configured"):
        pick_puller([], "")


def test_pick_puller_excludes_in_use() -> None:
    pullers = [
        {"puller_name": "gpu-01", "is_active": True, "is_stale": False, "status": "ok", "supported_models": ["llama3"]},
        {"puller_name": "gpu-02", "is_active": True, "is_stale": False, "status": "ok", "supported_models": ["llama3"]},
    ]
    assert pick_puller(pullers, "llama3", exclude={"gpu-01"}) == "gpu-02"


def test_idle_pullers_for_model() -> None:
    from article_factory.services.puller_selection import idle_pullers_for_model

    pullers = [
        {"puller_name": "gpu-01", "is_active": True, "is_stale": False, "status": "busy", "supported_models": ["llama3"]},
        {"puller_name": "gpu-02", "is_active": True, "is_stale": False, "status": "ok", "supported_models": ["llama3"]},
    ]
    idle = idle_pullers_for_model(pullers, "llama3")
    assert [p["puller_name"] for p in idle] == ["gpu-02"]


@pytest.mark.asyncio
async def test_get_registered_puller_on_cp() -> None:
    from unittest.mock import AsyncMock

    from article_factory.control_plane.client import ControlPlaneClient
    from article_factory.services.puller_selection import get_registered_puller_on_cp

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_puller = AsyncMock(
        return_value={
            "puller_name": "gpu-01",
            "is_active": True,
            "is_stale": False,
            "status": "busy",
        }
    )
    row = await get_registered_puller_on_cp(cp, "gpu-01")
    assert row is not None
    assert row["puller_name"] == "gpu-01"


@pytest.mark.asyncio
async def test_select_puller_for_model_integration() -> None:
    from unittest.mock import AsyncMock

    from article_factory.control_plane.client import ControlPlaneClient
    from article_factory.services.puller_selection import select_puller_for_model

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "gpu-01",
                "is_active": True,
                "is_stale": False,
                "status": "ok",
                "supported_models": ["llama3"],
            }
        ]
    )
    assert await select_puller_for_model(cp, "llama3") == "gpu-01"


def test_pick_puller_falls_back_to_active_busy() -> None:
    pullers = [
        {
            "puller_name": "busy-gpu",
            "is_active": True,
            "is_stale": False,
            "status": "busy",
            "supported_models": ["llama3"],
        },
        {
            "puller_name": "offline-gpu",
            "is_active": True,
            "is_stale": False,
            "status": "offline",
            "supported_models": ["llama3"],
        },
    ]
    assert pick_puller(pullers, "llama3") == "busy-gpu"


def test_pick_puller_no_match_raises() -> None:
    with pytest.raises(RuntimeError, match="No idle puller"):
        pick_puller(
            [{"puller_name": "p", "is_active": False, "supported_models": ["x"]}],
            "llama3",
        )


@pytest.mark.asyncio
async def test_get_registered_puller_empty_name() -> None:
    from article_factory.control_plane.client import ControlPlaneClient
    from article_factory.services.puller_selection import get_registered_puller_on_cp

    cp = AsyncMock(spec=ControlPlaneClient)
    assert await get_registered_puller_on_cp(cp, "  ") is None


@pytest.mark.asyncio
async def test_get_registered_puller_fallback_list() -> None:
    from article_factory.control_plane.client import ControlPlaneClient
    from article_factory.services.puller_selection import (
        get_registered_puller_on_cp,
        puller_is_registered_on_cp,
    )

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_puller = AsyncMock(side_effect=RuntimeError("down"))
    cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "gpu-02",
                "is_active": True,
                "is_stale": False,
                "status": "busy",
            }
        ]
    )
    row = await get_registered_puller_on_cp(cp, "gpu-02")
    assert row is not None
    assert await puller_is_registered_on_cp(cp, "gpu-02") is True


@pytest.mark.asyncio
async def test_get_registered_puller_list_fails() -> None:
    from article_factory.control_plane.client import ControlPlaneClient
    from article_factory.services.puller_selection import get_registered_puller_on_cp

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_puller = AsyncMock(side_effect=RuntimeError("down"))
    cp.list_pullers = AsyncMock(side_effect=RuntimeError("down"))
    assert await get_registered_puller_on_cp(cp, "gpu-02") is None
